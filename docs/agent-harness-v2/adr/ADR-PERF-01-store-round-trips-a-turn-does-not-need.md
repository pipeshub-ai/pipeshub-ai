# ADR-PERF-01: The store round trips a turn does not need

- Related: PERF-E3's store-command ratchets (`integration-tests/perf/slo_gates.py`, PERFC-F1), SLO #4, R-8 (lease and deadline predicates read the database's clock), CLEANUP-S4 (an ended run's budget account is closed), C5.05 (a run's cache-read share), WP88-FU-4 and WP88-FU-5.

## Context

WP-88's stage 12 found an ending commit at 17 store commands and an admission at 23.5, over PERF-E3's ratchets of 15 and 19. It also found admission p95 at 225 ms when 16 admissions of one org arrive in the same instant on one Node (SLO #4: 150). A census of the rig's commands by the request that sent them (`commit-cpu`'s attributed window, with each command's call site) found the growth in three places:

- **The database clock was asked on every read.** `mongoServerTime.read()` sent `hello` for `localTime` each time. Every admission asks it, and so do the budget ledger's transactions and several lease paths. In step, sixteen admissions asked sixteen times in the same instant, each a round trip on the admission's own path.
- **An ended run's budget was read once and closed in a transaction.** C5.05 read the account for its metric. CLEANUP-S4 then closed it in a transaction: a clock read, the account's update, a read of its open reservations and the commit. That is five commands, even when, as almost always, nothing was still reserved.
- **A new conversation's stream read back what its admission had just written.** The stream reads the session (access and title) and the run, which admission wrote in the same request.

## Decision

**D1. One reading of the database's clock is carried forward on the process's monotonic clock.** `carriedServerTime` asks `hello` once and answers reads for the next `SERVER_TIME_CARRY_MS` (5 s) as that reading plus the monotonic time since, in whole milliseconds. Reads that arrive while it is asked wait for the same answer. R-8 holds: the monotonic clock cannot be set or stepped, so a skewed or jumped local wall clock still moves nothing. A reading can be off by its own round trip and by the two clocks' drift over 5 s, well under a millisecond. The document store already keeps a 5 s offset (`CLOCK_OFFSET_TTL_MS`). Statements still use `$$NOW`.

**D2. Close answers the account it closed, outside a transaction unless something is still reserved.** `BudgetLedgerStore.close` returns the account as closed (null for a run that has none), and the cache-read metric reads it from there (`recordRunCacheRead`). On MongoDB, the close is one guarded `findOneAndUpdate` of the one account document, then a check for open reservations. Only if one is found does the expiry run in its transaction. This is safe without a transaction because a reservation's transaction writes the same account. Either it committed before the close, and the check finds it, or it conflicts with the close and, when retried, finds the account closed (`BUDGET_ACCOUNT_CLOSED`). PostgreSQL keeps its one unit, which now returns the account.

**D3. A turn that creates a conversation streams on what its admission wrote.** `RunAdmissionResult.createdSession` carries the title the store wrote the session with (`durableSessionTitle`). It is set only on the admission that created the session and is never stored in the receipt, so a replay reads what is stored now. `SessionRunService.admittedStreamAccess` opens such a turn's stream as its owner's with no read. A follow-up, a replay, or a run of another session or actor is read as `streamAccess` reads it.

**D4. One Node's MongoDB pool holds the in-step instant (R23-PERF, WP88-FU-5).** `APP_MONGO_DEFAULT_OPTIONS.maxPoolSize` goes from 10 to 64. Sixteen sessions in step put their admissions, commits and stream reads in the driver in the same instant, about 48 operations; with 10 connections they waited in the driver's queue. Connections open only on demand, so an idle Node holds no more than before. An interleaved A/B on the same loaded host (`commits-n16-instep --samples 20`): pool 10 admission p95 268.7 / 250.0 ms and commit p99 151 / 133.6; pool 64 221.2 / 186.9 and 111.2 / 98.2.

## As built

- Rig census on the same host and tree (`commit-cpu` attributed window, 30 turns): admission 22.0 → 18.27, ending commit 18.0 → 14.0, step commit 8.02 → 8.0. Each stays under its ratchet, and no ratchet moved.
- Admission's in-step path loses a round trip per admission, and the instant's sixteen share one clock read.
- D4: Node `mongo.service.test.ts` (WP88-FU-5: the connect call's pool holds 16 sessions × 3 operations).
- Tests: Node `mongo-time.test.ts` (WP88-FU-4/FU-5: carry, refresh, shared read, wall-clock jump, integer ms, failed read not carried), `budget-ledger-contract.ts` (WP88-FU-4: close answers the account) on every backend, `session-run-service.test.ts` (WP88-FU-4: no read for a created conversation; a follow-up, replay and other session are read), the execution-store contract (`createdSession` on a created admission, never on a replay).

## Deviations

None. D1 to D4 are built as decided.

## Owner amendments

None.

## Status

**Accepted** 2026-11-10 (FU-PERF).
