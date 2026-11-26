# ADR-RUN-05: Deleting a chat stops its run when the delete commits

- Related: E7 (session erasure, `backend/nodejs/apps/src/modules/agent-harness/application/session-erasure-steps.ts`
  step `cancel_runs`), SLO #7 (cancel to stopped p95 < 2 s), SEC-AZ-08 (a deactivation stops a person's runs through
  `cancelOpenRuns`, `run-stopper.ts`).
- Found by: R30-LAT QA-LAT-9 (QAFINAL-FU-LAT, erasure/latency).

## Context

Deleting a chat soft-deletes it and, in the same transaction, enqueues its erasure (E7). The erasure job's first step,
`cancel_runs`, is what stopped a run still going in that chat, so the run went on until the erasure worker's next pass
reached the job. On the lane stack QA-LAT-9 measured the confirm click to the run read as ended at 51.8 to 61.7 s
(six samples, p50 58.1 s), against SLO #7's 2 s for a stop. Meanwhile a run waiting on an approval kept its card in
Needs you, and a running one kept spending, for a chat the person had already removed.

Stopping the run at the delete (D1) was not enough: the second QA-LAT-9 run, with D1 in, measured 57.7 to 61.1 s
again. The cancel was journaled at the delete (`run.cancel_requested`, the erasure command id), and the worker's commit
that ends the run `cancelled` was then refused, `CONVERSATION_DELETED` (410): the commit carries the run's transcript,
the transcript has no conversation to go to, and QA21-F4 refuses any ending whose transcript cannot be written. The run
stayed `cancelling` until its lease lapsed and the sweeper ended it, about a minute later. So a deleted chat's run could
never end by its own commit, whoever stopped it.

The person's delete is a stop: nothing they could want of the run remains. A Stop and a deactivation already cancel at
once through `cancelOpenRuns`; only the delete waited for a background job.

## Decision

- **D1. The delete stops the run itself.** Once the delete's transaction commits, both delete routes (the default chat
  and an agent's chat) call `SessionRunStopper.stop` (`application/session-run-stopper.ts`, bound in the erasure
  binder, read by the routes through `sessionRunStopper(container)`). It cancels the session's active root run, and its
  children through it, by `cancelOpenRuns`, as Stop and a deactivation do.
- **D2. One command id with the erasure step.** The cancel's command id is `erasureCancelCommandId(session, run)`, the
  erasure step's own, so whichever comes second controls nothing again (receipt-idempotent).
- **D3. The erasure step stays the durable path.** A stop that fails, or never runs (the process dies between the
  commit and the call), is only logged; `cancel_runs` stops the run when the job runs, as before. The delete's answer
  does not change.
- **D4. A stopped run ends without its transcript.** A commit that ends a run `cancelled` commits when its transcript
  has no conversation to go to; the transcript is dropped, as erasure would drop it (`endsWithoutTranscript`,
  `domain/execution.ts`, in the MongoDB, PostgreSQL and in-memory execution stores, on the worker's commit and on a
  control's direct cancel). Any other ending of such a run (`completed`, `failed`) is still refused naming the cause
  (QA21-F4, EU-22).

## As built

- R30-LAT (`harness/v2-r30lat`): `SessionRunStopper` (`backend/nodejs/apps/src/modules/agent-harness/application/session-run-stopper.ts`), bound in
  `binders/erasure.ts`, read by `deleteConversationByIdWith` and `deleteAgentConversationByIdWith` through
  `sessionRunStopper(container)`; `endsWithoutTranscript` (`domain/execution.ts`) in the three execution stores.
- Tests: `r30lat-session-run-stopper.test.ts` (STOP-1..3), `session-erasure.mongo.itest.ts` ER-STOP, contract EU-23
  on MongoDB, PostgreSQL and in memory (fails without D4), and the browser's QA-LAT-9 `delete_run_stopped_ms`.

## Deviations

- The delete waits for one cancel command (one store transaction) before it answers.
- A child run still ends through its root's cancel; a run the stopper could not reach is stopped by erasure's
  `cancel_runs` as before (D3).

## Owner amendments

None from the owner.

- **2026-11-14, R50-JFIX: text after Stop is not the stopped answer (decided UX-first).** A Stop (or a delete, which stops through the same cancel) ends the answer where the person pressed it: the stopped answer keeps the text the model streamed before the worker was told of the cancel (UX3), and nothing the model sends after. The lane's /mcp and Slack cancel journeys showed the gap: the held model's reply ended while the worker's cancel check was reading the journal, the finished call won the race, its response was journaled after `run.cancel_requested`, and the client and the thread got the reply the person had stopped. The journal's order decides, not clocks across processes: in `_unless_cancelled` (the durable kernel), a model reply that ends while a cancel check that began before it is reading the journal is decided by that check, and a check that finds the cancel keeps only the deltas streamed when it began (`_CancelCheck`). A reply that ends with no check in flight was finished before the worker could know, and stands; a write's result is never discarded this way (only the model call is decided by the check). Tests: `test_r50jfix4_stop_drops_late_text.py`.

## Status

**Accepted** (2026-11-12, R30-LAT: SLO #7 applies to a delete as to a Stop; decided from first
principles, UX-first).
