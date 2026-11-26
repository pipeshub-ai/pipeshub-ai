# ADR-SEC-02: Every entry point binds the org; a store never binds the org its arguments name

- Supersedes: ADR ORGBIND in STATUS "WP-114 evidence" (the bounded fallback, accepted for WP-114).
- Related: ADR-OWD-03 (isolation and tenancy), ADR-OWD-10 (runtime placement and stores), main's org context (`backend/nodejs/apps/src/libs/context/orgContext.ts`, `libs/db/README.md`).

## Context

On PostgreSQL every unit of work runs as one org: row policies read `app.org_id`, which the unit takes from the org context bound in the async context. WP-114 ported every harness and control-plane store, but the worker's routes, the claim's per-run phase and the sweepers bound no org. So the stores bound one themselves: a call with no org bound ran as the org its arguments named (`inRunOrg`, `asOrgWhenUnbound`, `forSweptOrg`, `cpUnit`, `inDurableTransaction({ orgId })`, the `asOrg` calls in the erasure stores), and `orgOrJobsUnit` fell back to the jobs role. A call made while another org was bound was refused.

That fallback had two faults:

- It hid wiring faults. A route or job that forgot to bind still worked, as whatever org its arguments named, so nothing showed which entry points were unbound.
- It put the authority in the wrong place. The arguments of a store call are whatever the caller passes. The store cannot tell an org that came from a verified token or a job row from one that came from a request body.

## Decision

1. **The entry point binds the org, from server-side state.** It never takes the org from client input.
   - A user request: main's auth middleware.
   - A worker request after the claim: the attempt token's verified claims (`requireAttemptToken`).
   - A gateway's request: the run's verified gateway token (`BudgetLedgerService.asCaller`, `GatewayTokenService.asReporter` and `modelGrant`).
   - The claim's per-run phase: the run that the cross-org pick found (`ExecutionService.claimNext`, and the controller's attempt-token phase).
   - A sweep, scheduler or job: the row that its own cross-org read found (`forEnumeratedOrg`). This covers the run sweep, the next-run drain, the memory settle, the approver notices, the operator controls, the schedule firer, the retention sweep and each of its parts, the session and principal erasure jobs, the cost folds, the audit anchor and link watch, an edition's background workers, the placement republish, the erasure backfill and the control-plane data migrations (approvals, policy layers, capability), where the migration's own listing or the operator's `--org` names each org.
   - A platform event: the event's org, as main's consumers bind it (the connector trigger source).
   - A subscription callback (journal tail, live bus, run journal follower): the context it was registered in (`bindOrgContext`). It never runs in the poller's or publisher's context.
2. **Cross-org listing stays on the jobs role.** The run queue, the due schedules and the sweep pages are read as the jobs role, as main's outbox dispatcher reads its queue. Each row found is then worked in a context bound to its own org.
3. **A store call with no bound org is refused.** `requireBoundOrg` (`libs/repositories/agent-harness/durable-transaction/bound-org.ts`) throws `UnboundOrgError` when no org is bound and `OtherOrgBoundError` when another one is. Every per-org path goes through it:
   - `orgUnit`, before a connection is taken;
   - `inRunOrg`, `inBoundOrg` and `inBoundOrgUnit` (main's repositories);
   - `cpUnit` and the control-plane transaction;
   - `inDurableTransaction({ orgId })`, which is shared by both stores, so MongoDB refuses too. The MongoDB stores also still scope every query by the org they are given, as defence in depth.

   `orgOrJobsUnit` reaches the jobs role only inside a jobs unit that a sweep already opened.
4. **Worker-credential routes that name their org.** These are the claim by name, the grant auditor, the egress proxy's gateway token and the live layer. They run as the org they name (`asWorkerNamedOrg`). The credential is the deployment's and already holds every org, since claim-next hands it any org's run. The store then finds the run only if that org holds it.

### Exceptions, each with its reason

- **Lookups keyed by a secret.** These are `ambientUnit` (approval links, approver notices, outbox ids) and the data-export ticket consume. With no org bound, they look the key up as the jobs role. The key is a secret's digest and names no org, as main's `lookupUnit` does for its sign-in lookups.
- **Sign-in lookups in the org-deletion data.** These are `deletionAccount`, `orgDisplayName`, `findSignInOrg` and `findOrgBeingDeleted`, which run before any org is bound. Each binds the org that its own lookup found (main's `orgForKey` pattern).
- **The session-backup restore.** It binds the org of the archive it has verified whole. The archive is the job's own record of its org. The export is bound by the CLI from its `--org`.
- **The org context guard.** No new exemptions. `detachedInterval` and `spawnDetached` start their work with an empty context, and each tick binds per org as above. The `detached_work` and `direct_org_reads` counts do not rise.

### Consequences

- An entry point that forgets to bind fails at once, with a message naming the fault. It is never served as another org. A new background job binds per row, or its first store call is refused.
- MongoDB and PostgreSQL behave the same at the shared helper, so a binding fault shows on the default store too.

## As built

Built by WP-116 on `harness/v2-wp116` (from mainsync `f975c7396e`), commit `d8559ce992`; evidence in the project tracker, "WP-116 evidence".

- **The refusal** (decision 3): `requireBoundOrg`, `inBoundOrg` and `inBoundOrgUnit` with `UnboundOrgError` and `OtherOrgBoundError` in `backend/nodejs/apps/src/libs/repositories/agent-harness/durable-transaction/bound-org.ts`. It is called by `orgUnit` before a connection is taken, by `inRunOrg`, by `cpUnit` and the control-plane transaction, and by `inDurableTransaction({ orgId })` (`durable-transaction/index.ts`) on both stores. `orgOrJobsUnit` (`agent-harness/pg-documents/org-or-jobs.ts`) reaches the jobs role only inside a jobs unit a sweep opened. The fallbacks `asOrgWhenUnbound`, `forSweptOrg` and the erasure stores' `asOrg` are gone.
- **Sweeps, schedulers and jobs** (decision 1): `forEnumeratedOrg` in `backend/nodejs/apps/src/libs/context/orgContext.ts`, beside main's `forOrg`. It binds the org of the row a cross-org read found, leaving whatever context the caller had. Every sweep, job and data migration listed in decision 1 calls it per row.
- **Subscriptions** (decision 1): `bindOrgContext` in the same file. The journal tails (PostgreSQL, Mongo, in memory), the live buses (Redis, in memory) and the run journal follower call back in the context they were registered in.
- **Worker routes** (decisions 1 and 4): the attempt-token routes bind the token's verified claims (`requireAttemptToken`; claims with no org get a 401); the budget routes bind through `BudgetLedgerService.asCaller`; the gateways' grant and reports through `GatewayTokenService.asReporter` and `modelGrant`. The four deployment-credential routes that name their org (the claim by name and the grant auditor in `transport/internal-controller.ts`, the egress proxy's gateway token in `transport/gateway-token-controller.ts`, the live layer in `transport/live-layer-controller.ts`) run through `asWorkerNamedOrg` (`backend/nodejs/apps/src/modules/agent-harness/transport/internal-auth.ts`).
- **The claim**: claim-next tries each candidate of the cross-org pick as that candidate's org; its attempt-token phase (issue, tree read, release on failure) runs as the claimed run's org.
- **The session-backup CLI**: the export binds `--org`; the restore binds the org of the archive it verified whole.
- **Tests.**
  - `backend/nodejs/apps/tests/modules/agent-harness/org-binding.test.ts` (13): the refusal of a store call with no bound org (`UnboundOrgError`) or another org bound, on every shared helper; and one case per worker entry point (attempt token, claim-next's pick and per-run phase, the worker-named routes, the budget and gateway run tokens) showing that it binds the run's org.
  - `backend/nodejs/apps/tests/modules/agent-harness/org-binding-sweeps.test.ts` (24): one case per sweep, scheduler, job, event and subscription showing that each row's work runs as that row's org.
  - `backend/nodejs/apps/tests/modules/agent-harness/org-binding.pg.itest.ts` (4): the same refusal on PostgreSQL behind PgBouncer, for an execution read, a control-plane read and a durable transaction, before any row is touched; the same calls bound; a sweep's row work.
  - The PostgreSQL suites bind the org they mean, as an entry point would (`boundStore`, `scopedStore`, `inOrg` in `tests/modules/agent-harness/support/harness-pg.ts`). The cases that asserted the fallback assert the refusal.

## Deviations

None from the decision. Not yet shown, and tracked in STATUS:

1. No end-to-end harness run on `DOC_STORE=postgres` (WP114-FU-E2E): the bindings are proven per entry point by unit cases and the stores' PostgreSQL suites, not by a running stack.
2. The control-plane data migrations' binding has no case of its own; their unit suites pass and `control-plane-stores.pg.itest.ts` binds the org in its test transaction.
3. The Redis live bus and the PostgreSQL and Mongo journal tails carry the same `bindOrgContext` change as the in-memory ones, which are tested; they have no case of their own (they need a running service).
4. The erasure backfill and the legacy control-plane CLIs on PostgreSQL stay WP114-FU-CLIS.

## Owner amendments

None.

## Status

**Accepted** 2026-11-07: decided from first principles, long term (with main merged, PostgreSQL stores included), built by WP-116. It supersedes WP-114's ADR ORGBIND, the bounded fallback.
