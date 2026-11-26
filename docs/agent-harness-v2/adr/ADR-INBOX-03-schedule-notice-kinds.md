# ADR-INBOX-03: A schedule's notices have schedule kinds, and the counts carry the jobs at work

- Amends: ADR-INBOX-01 (the Inbox item kinds, the counts), ADR-INBOX-02 D1 (what is pushed). Related: classic jobs plan (`classic-jobs/PLAN.md`) §2, §4.3 ("Jobs 2") and §6 (the request budget).

## Context

The Inbox item kinds `workflow_run`, `workflow_failed` and `workflow_waiting` came from the new interface's workflows. Workflows are being removed, but classic schedules stay, and the Inbox maps a schedule's three notices (`AGENT_SCHEDULE_RUN_ENDED`, `AGENT_SCHEDULE_FIRING_REFUSED`, `AGENT_SCHEDULE_APPROVAL_WAITING`) onto those kinds. The plan asked the removal to prune the `workflow_*` kinds with the contract; pruning them would drop the schedule notices with them.

## Decision

**D1. The kinds are named for what writes them.** `schedule_ran` (a schedule's run completed), `schedule_failed` (it ended otherwise, or the firing was refused) and `schedule_waiting` (its run waits on an approval) replace the three `workflow_*` kinds in `inbox-item.schema.json` (`INBOX_ITEM_KINDS`), the Node domain, the frontend and the OpenAPI enum. Their pointer (`type: 'schedule'`), mute and subject fields are unchanged.

**D2. No migration.** Nothing stores a kind. A notice is stored as main's notification with its writer's `type`, and its kind is derived on every read (`noticeKind`), so every stored notice reads with the new kind at once. The frontend ships with the server, so no client of the old enum is left.

**D3. Only the kinds change.** The `workflows` filter, `subject.workflow` (the schedule's name), the refusal code `workflow_changed` and the client's words keep their names. They are a query value, a contract field and product words, not kinds, and renaming them is the workflows removal's to decide with its own aliases.

**D4. The counts carry the jobs at work (CJ14-FU-1).** The sidebar's Jobs entry shows how many of the person's jobs are working (§4.3), and §6 allows no added request on chat open. So the number rides on the counts every page already reads and is pushed: `InboxCounts.working`, optional and additive (`subjectVersion` unchanged). A server that does not count it sends none, and the client shows no count.
- *One rule.* A job at work is a child run in the list's `working` state that has started (`queued` is the page's Up next): `isWorkingJob` (`domain/run-lists.ts`). The frontend's Working section reads the same rule from the list's `state` and `status` (`statusOfPersonRun`, `sectionOf`). Both are tested against one table, `contracts/harness/vectors/working-jobs.json`, so neither can drift alone.
- *Bounded read.* The count reads the person's runs that have not ended (`activeRunsOfActor`, by the status index), which admission's per-person active-run quota bounds, and filters them by the rule. It adds no store method. A store without that read, or a failed read, leaves `working` out.
- *Live.* `ExecutionService` tells `onRunWorkingChanged` when a command moves a job into or out of the set. That happens when a claim starts it, a release puts it back in the queue, or a commit, control, sweep or failed run changes its state. A new job is queued, so its admission changes nothing. Replays are not told. The Inbox announces the run actor's counts through the same coalesced `announce`. A change of `working` does not re-read the Jobs list or the sidebar (the §6 refresh triggers are unchanged).

## As built

- Contracts regenerated (Node, Python, frontend); `pipeshub-openapi.yaml` in step.
- Tests: Node `inbox-domain.test.ts` (the three notice types map to the schedule kinds), `inbox-feed.test.ts`, `uib4-inbox.test.ts`; frontend `lib/harness-jobs/__tests__/inbox.test.ts`.
- D4: `inbox-counts.schema.json` `working`, regenerated, and `pipeshub-openapi.yaml` `InboxCounts.working`. Each count also reads the person's active runs. Tests: Node `cj14fu1-working-jobs.test.ts` (CJW-1..4); frontend `lib/harness-jobs/__tests__/cj14fu1-working-jobs.test.ts` (the shared table) and `jobs-nav-entries.test.tsx` (the entry's count, in words, hidden at 0 and without `working`).

## Deviations

None.

## Owner amendments

None: decided from first principles, long term, UX-first.

## Status

**Accepted** 2026-11-10.
