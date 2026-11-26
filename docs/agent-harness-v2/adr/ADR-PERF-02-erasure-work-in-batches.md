# ADR-PERF-02: Erasure works in batches, not per record

- Package: R31-LAT (STATUS R30-LAT-FU-2).
- Related: ADR-PERF-01 (store round trips a turn does not need), E7 (session erasure), CLEANUP-G1 (org deletion),
  CLEANUP-G4 (abandoned forks), QA15-F3 (legacy run keys), `frontend/tests/e2e/perf/volume-budgets.json` (`stores`:
  `session-erasure-job`, `org-sessions-page`, `abandoned-forks`, pre-registered before their first measurement).

## Context

R30-LAT counted erasure's store commands and saw loops it did not budget. On a MongoDB replica set, before this ADR:

- A session's erasure job read the session's runs in each step that needed them: five reads in one pass of the
  rig, seven with LiveBus and traces composed.
- The journal step digested each run key an older build stored with one `updateOne` per run: 56 commands for a
  session of two such runs, 1,060 for 1,000.
- An org deletion's page of session ids (`MongoOrgData.sessionsAfter`) ran an unbounded `distinct` over three
  collections per page: 3,750 ids sent for a first page of 500 out of 1,500 sessions, O(N²/limit) over the org.
- The retention sweep erased each abandoned fork in its own transaction: 7 commands for two forks, 452 for 150.

The shared fault: each step did per record what the store can do per batch, and each step re-read what an earlier
step of the same pass had read.

## Decision

D1. **One pass of the erasure runner shares what does not change.** The runner gives every step of a pass an
`ErasurePass`. The steps where the runs may still change (`cancel_runs`, `await_runs_terminal`) read them and record
the read; every later step of the pass reuses it. Once the runs have ended no step adds one. A new pass (after a
wait, a crash or another claim) starts empty, so nothing stale outlives its pass.

D2. **Per-record writes go in batches of 1,000** (`ERASE_BATCH`): legacy run keys by `bulkWrite` on MongoDB and one
`UPDATE … FROM unnest` on PostgreSQL. Each update still names the key it read, so a key changed since is left alone.
A session's runs are read in batches of 5,000 (`SESSION_RUNS_BATCH`), one round trip for thousands of runs.

D3. **A page reads a page from each source.** `sessionsAfter` reads at most `limit` ids after the cursor from each
source (`find` sorted and limited where a session has one document, an aggregation of distinct sessions for runs),
and returns the first `limit` of their union, as PostgreSQL's `UNION … LIMIT` already did.

D4. **An org's abandoned forks go in one transaction.** `AbandonedForks.dropMany` removes, of one org's listed forks,
those still uncommitted and begun before the cutoff, and answers them; their erasures are enqueued with
`enqueueMany` in the same transaction. A commit racing it still wins (MongoDB: read and delete in one transaction, a
write conflict retries; PostgreSQL: one `DELETE … USING unnest … RETURNING`).

## As built

- `backend/nodejs/apps/src/modules/agent-harness/ports/session-erasure.ts` (`ErasurePass`),
  `backend/nodejs/apps/src/modules/agent-harness/application/session-erasure.ts`,
  `backend/nodejs/apps/src/modules/agent-harness/application/session-erasure-steps.ts`;
  `backend/nodejs/apps/src/libs/repositories/agent-harness/session-store-eraser/batches.ts` and both erasers;
  `backend/nodejs/apps/src/libs/repositories/agent-harness/principal-erasure/mongo.ts` (`sessionsAfter`);
  `backend/nodejs/apps/src/modules/agent-harness/ports/session-forks.ts` (`dropMany`), both abandoned-fork stores
  and `backend/nodejs/apps/src/modules/agent-harness/application/retention-sweeper.ts`.
- After, same rig: runs read twice a pass; 52 → 52 commands from 2 to 1,000 legacy-keyed runs; at most 1,500 ids
  per page (500 from each of three sources); 5 → 5 commands from 2 to 150 forks
  (`backend/nodejs/apps/tests/modules/agent-harness/erasure-volume.mongo.itest.ts` VOL-3..5). PostgreSQL:
  `session-store-eraser.pg.itest.ts` (1,200 legacy keys digested in batches) and `session-forks.pg.itest.ts`
  (`dropMany`'s races).

## Deviations

- On MongoDB, a page of runs' distinct sessions still has the server visit every distinct session after the cursor
  (`DISTINCT_SCAN` on `org_session`), though it sends only `limit`; the transfer is bounded, the server's key walk is
  not.

## Owner amendments

None yet.

## Status

**Accepted** 2026-11-12: decided from first principles, long term, UX-first.
