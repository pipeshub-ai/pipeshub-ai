# ADR-SCH-02: Schedules run whether or not an org uses the agent harness

- Amends: P8a (TR-02..TR-07, the schedule, its firings and the firer), ADR-SCH-01 (a schedule asked for in chat),
  ADR-OPS-08 (one org harness predicate), HARNESS-OFF.md (what an install without the harness has).
- Related: ADR-OPS-06 (one harness switch), ADR-INBOX-03 (schedule notices), main #4313 (writes that act for the
  person ask), ADR-SEC-02 (org binding).

## Context

A person asked a chat "tell me a joke after 2 mins". Scheduling needed three switches: the server's harness switch,
the org's Labs **Agent harness** and Labs **Scheduled runs**. The schedule routes are mounted only behind the harness
switch, a create is refused unless the org's runs are durable, the firer starts only inside the harness's own
composition, the Schedules page appears only with the harness on, and `schedule_create` exists only in harness runs.
So an org on classic chat, which is every org by default, has no scheduling at all: the feature people expect from a
chat ("remind me", "every Monday summarise…") depends on opting into a Beta execution engine.

Only one piece of scheduling is the harness's: what a firing *does*. The schedule, its trigger, its slots, its
firings, the firer's exactly-once claim, its notices and the Schedules page are product features over the document
stores; the firer already reaches its runner through one port (`ScheduledRunAdmission`, with a status and a digest
reader beside it).

## Decision

**D1. Scheduling is a product feature with its own gate.** An org has scheduling when Labs **Scheduled runs**
(`ENABLE_SCHEDULED_RUNS`) is on for it; the agent harness plays no part in that decision. The schedule and trigger
routes are mounted on every install and answer "not found" while the org's flag is off; the firer starts with the app;
the Schedules page and the agent builder's schedules appear on the flag alone. ADR-OPS-08's predicate still decides
everything that *is* the harness (durable runs, engines, the harness's own surfaces); `orgSchedulesOn` decides
scheduling, and both are checked where they are read, never cached across a request.

**D2. A firing is handed to a runner, chosen per firing.** The firer talks to one port, `ScheduleRunner`:
`start(firing) → {runRef}`, `status(runRef) → queued | running | waiting_for_you | completed | failed | cancelled`,
and `summary(runRef)` (the digest the notice carries). Two implementations: `HarnessScheduleRunner` (today's durable
admission, status and digest, unchanged) and `ClassicScheduleRunner` (D3). The firing picks one when it starts, by the
org's harness predicate at that moment: harness on → durable run, off → classic turn. The firing records which runner
and its `runRef`, so a firing is watched and told by the runner that started it even if the org changes path while it
runs. Everything the firer does around a run (claim, overlap, catch-up, deadline, retries, notices, history) stays the
same for both.

**D3. A classic firing is the chat turn a person would start.** `ClassicScheduleRunner` starts the same classic
assistant or agent turn the chat UI starts, through the same pipeline (the turn's own checks: scopes, agent access,
the agent's knowledge, tools and model, the org's rules), as the schedule's owner, in a new conversation named after
the schedule and marked as made by it. It authenticates with the actor credential the firer already mints for durable
runs (the owner's real role, re-read at each firing; a disabled or departed owner gets no run), valid for the firing's
deadline. It runs in the app's own process with no browser: the pipeline's response is written to a sink the runner
reads, not to a socket. The run ends when the turn's answer is saved (or the turn fails, or the deadline passes); its
`runRef` is the conversation and message.

**D4. A scheduled turn that needs the person waits for them.** On classic chat a call that needs approval is blocked
when nobody is watching. A scheduled turn has a person who will read it, so its asks wait instead: the pending call is
kept until the firing's deadline, the conversation shows its approval card when the person opens it, the run reads as
`waiting_for_you`, and the person is told on the schedule's notice channels, as a durable run that waits for approval
is. Approving in that conversation resumes the call through classic chat's own resume; an ask unanswered by the
deadline ends the run as expired and says so.

**D5. Classic chat can make a schedule too.** `schedule_create` is offered on classic chat when the org has
scheduling, with the same arguments contract and the same pre-card checks as the harness tool (one shared module),
asking the person every time through classic chat's approval card (no remember, no conversation-wide allow). On
classic chat the call that creates the schedule runs inside the person's own request that approved it, so it creates
through the ordinary schedules API with that request's own credential: no journal binding is needed there. The
harness keeps ADR-SCH-01's journal-bound route. Either way the schedule is the person's, made with every check the
Schedules page makes, and the tool's answer says what will run, when, and where.

**D6. What depends on the runner is said, not hidden.** A cost cap (`maxCostUsd`) needs the harness's budget ledger:
it is offered and enforced where the org's runs are durable and not offered otherwise (a schedule made on the harness
keeps it; if it later runs classic, the run says the cap does not apply). Everything else a person sets (name, what
to ask, when, notices, overlap, catch-up, the agent and its version) means the same on both.

## As built

- Gate (D1): `orgSchedulesOn` in `backend/nodejs/apps/src/modules/agent-harness/config.ts` (Labs
  `ENABLE_SCHEDULED_RUNS` alone, fail closed), used by `ScheduleService` and `ScheduleFirer`. `app.ts` mounts the
  schedule and trigger routers on every install, ahead of the harness namespace's guard; each schedule route except
  delete checks the flag after authentication and before validation (`schedulesOn` in
  `transport/schedule-routes.ts`), so an org without scheduling reads `404 SCHEDULED_RUNS_DISABLED`. The Labs flag
  is no longer a harness flag (`configuration_manager/constants/constants.ts`), so it is listed, answered and saved
  with the harness switch off. `ScheduleService` no longer refuses a create for an org on classic chat
  (`SCHEDULED_RUNS_UNAVAILABLE` is no longer raised). `startScheduleServices` (`composition.ts`) builds the schedules'
  indexes (`migrateScheduleStore`) and starts the firer on every install; the harness's background services no longer
  start it.
- Runner port (D2): `ports/schedule-runner.ts` (`ScheduleRunner`, `ScheduleRunStatus`); `HarnessScheduleRunner`
  (`application/harness-schedule-runner.ts`) wraps `RunAdmissionScheduledRuns`, the run status reader and the run
  timeline's digest. The firer picks the runner per firing by `orgHarnessOn` when it starts and settles the firing
  with `runner`, `runRef` and `costCapIgnored` (all three schedule stores; no migration: the PostgreSQL firing is a
  `doc` column). Watching, overlap, notices and digests go through `firingRun(firing)`, a firing from before runners
  reading as the harness's.
- Classic runner (D3): `ClassicScheduleRunner` (`application/classic-schedule-runner.ts`) and
  `RepositoryClassicConversations` (`adapters/classic-schedule-conversations.ts`, the conversation repository of the
  configured document store). `InProcessClassicTurns` (`enterprise_search/utils/in-process-turn.ts`) runs the chat
  routes' own handlers (`createScheduledTurnPipelines` in `es.routes.ts`: authentication, scope, validation, default
  model, the legacy-turn metrics and `streamChat` or `streamAgentConversation`) on a request it builds, writing to a
  `TurnResponseSink`. The controller names the conversation after the schedule and records
  `metadata.origin {kind: schedule, scheduleId, firingId, scheduleName}`, which the conversation detail answers as
  `origin`. The actor credential's lifetime is a parameter of `ActorAuthority.authorize` (`ttlS`, the firing's
  deadline for a classic turn).
- One conversation per firing: a classic turn opens its conversation under an id derived from the org and the firing
  (`scheduledConversationId`), and both conversation stores refuse a second session under a caller's id
  (`SessionExistsError`: Mongo's unique `_id`, PostgreSQL's `ON CONFLICT (org_id, id) DO NOTHING`). A start that finds
  that conversation (a retry after a failed settle, a firing retaken after a crash, two firers at once) answers it as
  the run and starts no other turn; the watch then reads whether it is still running or ended.
- Headers to the query service: `x-pipeshub-schedule-firing` and `x-pipeshub-ask-deadline`
  (`transport/scheduled-turn-mark.ts`) are sent only through `AICommandOptions.scheduledTurnHeaders`, set by the two
  stream handlers from the runner's mark (a `WeakMap` keyed by the request object the runner made); a client's own
  copies are dropped by `resolvedProxyHeaders` and by the command's allowlist.
- Notices (D4, D6): a classic `waiting_for_you` tells the person as `approvalWaiting` does; `RunEndedNotice` and the
  approval notice carry `runner`, and a classic notice has no run id (the Inbox opens the conversation). A run that
  ended `ask_expired` or ignored a cost cap says so in its in-app notice (`runEndCaveats`). The firing view answers
  `runner` and `costCapIgnored`; OpenAPI documents both, the conversation `origin` and the cap's scope.
- Off checks: `deployment/e2e/harness_off_check.py` expects Labs to list scheduled runs and the schedule routes to
  answer `404 … not enabled for this organization` with the harness switched off; `HARNESS-OFF.md`, `FLAGS.md` and
  `contracts/harness/flags.json` say the same.
- Tests: `backend/nodejs/apps/tests/modules/agent-harness/sch02-schedules-without-harness.test.ts` (gate, runner
  choice, path change mid-run, overlap, notices, statuses, digest), `tests/modules/enterprise_search/scheduled-classic-turn.test.ts`
  (the real controller against a fake query service: headers, origin, completed, waiting, failed, ask expired, refusals),
  the store contract's SCH-02 case, the routes' flag-off case and the Labs cases in `cm_controller.test.ts`.

## Deviations

- `runRef` of a classic run is its conversation, not the conversation and message: the run is the firing's new
  conversation, and an approval resumed there adds the person's own turn and its answer, so the run's status is read
  from the conversation's latest answer.
- Cost cap (D6): the API keeps accepting `maxCostUsd` on create whatever the org's path (a schedule made while the
  harness was on keeps it), so the API does not refuse it; offering it only where runs are durable is the
  Schedules form's. A classic firing records `costCapIgnored` and its notice says the cap did not apply.
- A durable admission that routes the firing to the classic path (`rollout_legacy`: the operator's stop, a store not
  ready) runs it as the classic turn instead of refusing it, as the same chat would have run.
- An agent schedule's version pin applies on the harness only: a classic turn runs the agent as saved, as every
  classic agent chat does (HARNESS-OFF, OWD-14).
- With no classic runner bound (a deployment without the chat module) a firing whose org is on classic chat is
  refused `runner_unavailable`.
- Scheduling needs the document store's multi-document transactions: a slot is claimed once by inserting its firing
  and moving the schedule's cursor in one transaction, and a create counts the org's and the person's schedules in
  one. The default install runs MongoDB as a replica set, and PostgreSQL always has them. An install that runs MongoDB
  standalone (`REPLICA_SET_AVAILABLE=false`) has no scheduling even with Labs **Scheduled runs** on: the firer is not
  started (the app logs why at start-up), and a create or a run now is refused `503
  SCHEDULED_RUNS_STORE_UNAVAILABLE`, which tells the person "Scheduled runs need a database setup this server doesn't
  have. Ask your administrator to run MongoDB as a replica set" (`ScheduleService`, `startScheduleServices` in
  `backend/nodejs/apps/src/modules/agent-harness/composition.ts`). Lists and reads still answer; there is nothing to fire.

## Owner amendments

None.

## Status

**Proposed** (2026-11-27), owner direction: "schedule should run independently of the new agent harness",
decided from first principles, long term, UX-first.
