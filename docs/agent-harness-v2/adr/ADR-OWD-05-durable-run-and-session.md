# ADR-OWD-05 — The durable unit: a run per turn, the conversation as the session

## Context

[OWD-5](../one-way-doors.md) makes the session a durable resource, independent of any HTTP request, with a state machine (README §4.1), idempotent turns and effects, and the native PipesHub runtime as one harness kind. The spec models a long-lived session with the states PROVISIONING, SETUP, READY, IDLE, SUSPENDED, RESUMING and so on.

## Decision

Adopt OWD-5's intent with the model that was built and proven:

- **The durable unit is a run, one per turn.** A run is admitted, claimed under a fenced lease, driven and ended. It has its own journal stream and state machine.
- **The conversation is the session.** It carries identity, participants, workspace and history across runs. It is idle when no run is active.
- **Suspend and resume are run-level.** After a turn the sandbox is kept warm for `keep_warm_s`, then released. The next run restores the engine checkpoint and workspace in a fresh sandbox. A stalled or crashed run is re-driven from its last checkpoint.
- **One RunDriver serves both executors** (`executor: loop | engine` in `contracts/harness/core/run-spec.schema.json`), so the native runtime is one harness kind.
- Turns are idempotent by idempotency key, and effects by EffectLog command id.

This is an accepted divergence from the spec's session state machine (UX-first, 2026-10-26). The crash and recovery suites below pass on it, and it keeps the existing chat API, where a conversation already is the session ([ADR-OWD-13](ADR-OWD-13-public-api-naming.md)).

Mapping of the spec's session states: PROVISIONING/SETUP/READY are the run's `queued` → `running` with sandbox acquisition inside the run; IDLE is a conversation with no active run; SUSPENDED is a released sandbox with a saved checkpoint; RESUMING is the next run's restore.

## As built

Audited at `959c3bda72`. Decision rows: the v2 architecture report D2, D5, D19, D20; [ARCHITECTURE.md](../ARCHITECTURE.md) §3.1 #4 and #8; P1a.

- Run state machine: `backend/nodejs/apps/src/modules/agent-harness/domain/execution.ts` (`WORKER_TRANSITIONS`: queued, running, waiting_approval, paused, recovering, needs_reconciliation, cancelling, completed, failed, cancelled).
- Passing in the gate:
  - `backend/nodejs/apps/tests/modules/agent-harness/execution-store.model.test.ts` "QA13-6 the run state machine against the in-memory store: every command answers and leaves the store as the model says"
  - `backend/python/tests/unit/harness_core/durable/test_crash_schedules.py` (63), `backend/python/tests/unit/harness_core/engines/durable/test_crash_points.py` (54, including `test_ek14_a_worker_killed_at_each_boundary_resumes_without_duplicates`), `backend/python/tests/unit/harness_core/durable/test_qa24_recovery_table.py` (22)
  - `backend/nodejs/apps/tests/modules/agent-harness/support/execution-store-contract/admission.ts` "replays an identical admission and rejects a changed payload"

## Deviations

- **C2.02 (SL-05), ADR half.** Decided: run status plus conversation is the state model, and the spec's session states are not added as a separate machine. Proven today by the QA13-6 model test above. Left: the table-driven SL-05 test that every illegal run transition is rejected, in WP-04 CONF-TIMING (Wave 1).
- A session-level lifetime limit is per run, not per session (C2.12, SL-10). Accepted with the model above.
- **C8.08 (BU-06)**, amended here (UX-first, 2026-10-26): a stalled or runaway run ends the turn with an answer from what it gathered, and the user continues in the next run, instead of a separate paused state. Thresholds (2026-10-30, WP-27): 6 tool rounds in a row of only errors or empty results, or 4 rounds of the same calls with the same results, end the run on one answer-only turn flagged `truncatedBy: stall`, on both loops and on engines (`harness_core/kernel/core/stall.py`).

- **CH-14 (R23-CHAOS, 2026-11-11): one clock for store instants, on both sides.** Node's lease and deadline predicates read DB time (R-8), but the worker compared the run's deadline with its own wall clock and minted every yield's `notBefore` on it, and Node minted the payload-store yield's `notBefore` on its process clock. A node clock stepped an hour ahead cut a run's working time short (the tool time bound read zero) or parked a retry for the hour. Decided: an instant the store compares with its own clock is minted and read on the store's clock. The worker carries the claim's store reading (the claimed run's `updatedAt`, written with DB time by the claim) on its suspend-aware clock (`Claim.store_time`, `harness_core/clock.py` `StoreTime`); Node mints from `ExecutionStore.now()`. Wall clocks stay only where the instant is the outside world's (token and credential expiry). Tests: `tests/unit/harness_core/durable/test_ch14_clock_jump.py`, `test_node_workspace_contract.py::test_ch14_node_*`, `degraded-modes.test.ts` "CH-14 …", J-5 `db-time-leases.mongo.itest.ts`.

## Owner amendments

None. The quality bar (the harness runs through the existing chat APIs) is why the conversation is the session.

**R34-FIX D-INPUT-1 (2026-11-13, R33JGAPS-FU-1).** A turn a
chat route accepts is a turn the durable route serves, unless the field it sends is one only today's loop can serve. Every
field the chat stream routes accept has a durable role (`chatTurnFieldRole`): a run input, served the same way without
it, read by routing, or listed in `LEGACY_ONLY_FIELDS` with the reason (Compare models, a parked legacy approval). A
field a client starts sending therefore never moves its turns to today's loop unnoticed: the contract test drives the
bundled `@pipeshub-ai/mcp` SDK's own `pipeshub_chat` request through the route's validation and `chatTurnInput`.
`keywordSearchBudget` (`short` | `full`, the one schema `keywordSearchBudgetSchema`) means on the durable path what it
means on today's loop, the knowledge searches' keyword wait, and a child run inherits it; it is a guarded input field
(`keyword_search_budget`, as G-52's locale), so an older worker is never sent it and runs the search as before.

## Status

**Accepted** (2026-10-26). Built and tested. The SL-05 table test lands in WP-04.
