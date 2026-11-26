# ADR-OWD-16 — Workspace ownership and sharing

## Context

[OWD-16](../one-way-doors.md): can persistent workspaces be shared across principals, or are they strictly per principal? A shared workspace mixes data fetched under different ACLs. Once teams depend on it, reverting breaks them; allowing it carelessly is a data-leak class.

## Decision

Adopt OWD-16. In v1 a workspace belongs to one principal. A shared project workspace exists only under a project service-account principal whose ACL is the project's, and a user's data enters it only by explicit export. In a shared conversation, engine work is the owner's alone; "continue privately" forks into a new workspace and key.

## As built

Audited at `959c3bda72`. Decision rows: [ARCHITECTURE.md](../ARCHITECTURE.md) §4.9 ("v1: engine work in a shared session only by the owner"); the implementation plan D33 (`ws:{sessionId}` with a per-session key); SS0–SS3.

- Passing in the gate:
  - `backend/nodejs/apps/tests/modules/agent-harness/session-roles.test.ts` "EO-1: only the owner may start engine work; a viewer stays read-only"
  - `backend/nodejs/apps/tests/modules/agent-harness/collaborator-turns.test.ts` "collaborator, engine, true → SHARED_SESSION_ENGINE"
  - `backend/nodejs/apps/tests/modules/agent-harness/workspace-sealing.test.ts` (SS3 WS-1, a fork gets its own workspace key)

## Deviations

Likely contradiction, with no recorded decision: with `ENABLE_COLLABORATOR_TURNS` on, a collaborator's **loop** run gets `run_code`'s session workspace, `ws:{sessionId}`, which is the owner's (`backend/python/app/harness/assembly/assembler.py`, no actor check). Data fetched under two principals' ACLs could then mix in one workspace. **The code changes** in WP-07 PRINCIPALS + SHARED-WS (Wave 2): first a test that a collaborator's run cannot reach another principal's workspace, then the fix (no session workspace for collaborator runs, or a per-actor fork). Project service-account workspaces are not built and are not needed until a project principal exists.

## Owner amendments

None. Collaborator turns stay behind the Labs flag `ENABLE_COLLABORATOR_TURNS` (default off).

## Status

**Accepted** (2026-10-26). Built for v1 in WP-07: a collaborator's run, and its children, work in the collaborator's own workspace (`ws:{sessionId}:p:{actorId}`); Node's workspace routes refuse a run any other principal's workspace.
