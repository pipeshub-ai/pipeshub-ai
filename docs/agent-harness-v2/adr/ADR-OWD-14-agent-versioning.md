# ADR-OWD-14 — Agent versioning and what runs and schedules pin

## Context

[OWD-14](../one-way-doors.md): are agent definitions edited in place, or published as immutable versions? Schedules, audit ("which instructions acted?"), replay goldens and staged rollout all refer to a version. Adding versions after triggers exist forces a migration of every trigger and loses historical attribution.

## Decision

Adopt OWD-14. Agents get immutable published versions plus a draft. Each run records the `agent_version` it ran. A schedule pins a version, with an explicit opt-in to "latest published". Rollback re-publishes an old version. Existing agents are migrated to version 1 (GA-01).

## As built

Audited at `959c3bda72`; built in WP-21 (branch `harness/v2-wp21-agentversions`).

- **The draft** is the agent document and its edges, edited in place as before.
- **Published versions** live in the `agentVersions` graph collection (`backend/python/app/agents/versions.py`), on both graph backends through the generic provider surface.
  - One document per version, numbered 1, 2, 3… per agent and created once with `ensure_nodes`, never written again.
  - The definition is frozen as canonical JSON with a digest: everything but the agent's identity (owner, sharing, deletion), which is always read live.
  - Racing publishers each claim their own number.
- **Publish, rollback, list and read** are Python routes, proxied by Node as `/api/v1/agents/{agentKey}/versions…`.
  - A save publishes unless it asks to stay a draft (`publish: false`).
  - Rollback (rollback resets the draft) rewrites the draft to the old version's definition, discarding unpublished changes, then publishes it as the next number. The answer says when changes were discarded (`discardedUnpublishedChanges`).
  - Owner, sharing, deletion and owner team are never rewritten.
  - Ordering guarantee: the draft is written first and published only from the draft as it reads back. A failure part-way leaves the live version as it was, and the draft can no longer hold the discarded edits. Each section is replaced whole, so a retry finishes the rollback. The digest ignores the graph's bookkeeping on re-created nodes, so the new number has the old version's digest when every tool and source still exists.
- **Runs pin a version number.** `x.pipeshub.agent.definitionVersion` is now the version number (`contracts/harness/ext/pipeshub/run-spec.schema.json`).
  - The run-definition route builds the grant from that version: the latest, or `?version=N` for a schedule.
  - The worker runs that version's prompts, tools and knowledge (`backend/python/app/harness/assembly/tool_environment.py` `pinned_agent_version`).
  - A run admitted before versions pinned an edit time and runs the live agent.
  - A pinned version the store does not hold (not yet migrated, a graph restored without it) never refuses a run. The route and the worker take the latest published version, and an agent with none gets version 1 from its live definition. The pin's grant still narrows what the run may use.
  - Legacy chat runs the latest version.
- **Sessions record the version.** Every run records `agentVersion`, and the run view shows it, so a session records the version of each answer.
- **Schedules pin a version** (`agentVersion`: a number, or `latest` by explicit opt-in).
  - At creation the pin is the latest version the owner may run.
  - Only the owner moves it (`PUT /api/v1/agent-harness/schedules/{id}/agent-version`).
  - A schedule from before versions reads as version 1.
- **GA-01 migration.** Every existing agent gets version 1 at startup (`backend/python/app/migrations/agent_versions_migration.py`), or on its first read, whichever comes first.
- **Erasure.** An erased org's versions are erased page by page with its other query-service data.
- **Tests:**
  - `backend/python/tests/unit/agents/test_owd14_agent_versions.py`
  - `backend/python/tests/unit/api/routes/test_ga01_agent_version_routes.py`
  - `backend/python/tests/unit/harness/assembly/test_owd14_pinned_agent_version.py`
  - `backend/nodejs/apps/tests/modules/agent-harness/owd14-agent-versions.test.ts`
  - the schedule-store contract, run against the in-memory store and Mongo

## Deviations

None from the decision. Choices made where the OWD is silent, open to the owner:

- **Chats follow the latest version per turn.** A session records the version of every answer; it does not stay on the version it started with. G-01 asks a session to record its version; only schedules pin.
- **Saving publishes until the builder has a publish step.** The draft-only save exists in the API (`publish: false`). The builder UI for publish, draft, rollback and the version list is a static mock, not built.

## Owner amendments

- **Rollback resets the draft.** A rollback rewrites the draft to the version rolled back to, so a later save of old edits can never silently undo it. Tests: `backend/python/tests/unit/api/routes/test_ga01_agent_version_routes.py`, `TestRollbackResetsTheDraft`, on both graph backends' fakes.
- Also confirmed:
  - A save still publishes until the new builder UI ships.
  - Chats follow the live version per answer.
  - Schedules move only when their owner moves them.

## Status

**Accepted** (2026-10-26). Built in WP-21: immutable published versions, draft saves, rollback, run and schedule pins, sessions recording each run's version, and the GA-01 migration. A rollback resets the draft. The builder UI is a mock, not built.
