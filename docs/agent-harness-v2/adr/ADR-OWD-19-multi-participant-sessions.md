# ADR-OWD-19 — Multi-participant session semantics

## Context

[OWD-19](../one-way-doors.md): may users other than the principal steer or approve a session, and with what access? If a steerer could direct an agent running with someone else's access, that is privilege escalation built into the API.

## Decision

Adopt OWD-19, with the principal fixed per run:

- **Each run acts as its sender, narrowed by the agent's grant.** The principal is immutable for the run. This is stricter than "immutable per session": nobody steers with someone else's access, because a collaborator's turn runs as the collaborator.
- Participants have explicit roles: owner, collaborator, viewer (`backend/nodejs/apps/src/modules/agent-harness/domain/session-roles.ts`). Approval stays with the run's actor; a viewer cannot approve.
- Steering messages are attributed (`by` on `input.queued`).
- "Take over" is a fork: "continue privately" starts a new conversation under the new principal.
- Steerers need access ≥ the session's classification once classification exists ([ADR-OWD-15](ADR-OWD-15-data-classification.md)).

## As built

Audited at `959c3bda72`. Decision rows: [ARCHITECTURE.md](../ARCHITECTURE.md) §4.9; SS0, SS1, SS2, SS3.

- Passing in the gate:
  - `backend/nodejs/apps/tests/modules/agent-harness/collaborator-turns.test.ts` "AP-1" (Bob's run: only Bob approves; Alice, the owner, may only deny) and "AP-2: Alice's run: Bob, a collaborator, may neither approve nor deny"
  - `backend/nodejs/apps/tests/modules/agent-harness/support/collaborator-turns-contract.ts` "CA-2: a collaborator's turn runs as the collaborator, with sessionRole collaborator"
  - `backend/nodejs/apps/tests/modules/enterprise_search/session-fork-routes.test.ts` (SS3 fork routes, FK-13)

## Deviations

- Principal per run rather than per session: accepted above (stricter).
- The role set is narrower than the spec's viewer/commenter/steerer/approver: there is no commenter role and no approver-only role; a collaborator steers, and approvals stay with the run's actor. Accepted (UX-first: fewer roles to explain, and no role grants another person's access).
- "Access ≥ classification" waits for OWD-15, in WP-22.

## Owner amendments

None. Collaborator turns are behind the Labs flag `ENABLE_COLLABORATOR_TURNS` (default off); approvals follow the approvals rule (toolset writes run, deletes and sends ask).

## Status

**Accepted** (2026-10-26). Built and tested. The classification check follows WP-22.
