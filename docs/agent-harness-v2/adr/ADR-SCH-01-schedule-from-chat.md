# ADR-SCH-01: A schedule asked for in chat

- Builds: "do this later" from a chat ("tell me a joke in 5 minutes", "every weekday at 9 summarise my open Jira
  issues"), on the schedules of P8a (TR-02..TR-07).
- Related: ADR-WF-01 D7 and ADR-AGB-01 D1 (built by asking, set aside 2026-11-10 with the new interface),
  ADR-OWD-09 (approvals; layers only narrow), main #4313 (writes that act for the person ask), ADR-OPS-08 (one org
  harness predicate), ADR-SEC-02 (org binding), ADR-INBOX-03 (schedule notices).
- Reuses the planning of two earlier designs. [loom](https://github.com/pipeshub-ai/loom)'s scheduling plan: its
  one-shot `After` trigger, which fixed the same "tell me a joke after 2 minutes" request, and the rule "never invent
  a schedule the request did not ask for". The `nl-workflow` branch's task engine: `task_manage` with a `one_time`
  trigger and a `fire_at` time, its misfire policies, and "confirm the interpreted schedule with the person in plain
  language, never create silently". Neither engine is brought in: PipesHub has one scheduler, the schedule firer.

## Context

A person asked a harness chat "can you tell me a joke after 5 mins" with scheduled runs switched on. The run
answered at once and made nothing: the chat's agent has no tool that makes a schedule, and a schedule cannot run
once. Schedules are made only on Workspace → Schedules or `POST /api/v1/agent-harness/schedules`, as a cron (at
most once every 5 minutes), a webhook or a connector event. That route takes the person's own credential and
refuses a service caller, and no Python code calls it.

## Decision

**D1. A schedule can run once.** A new trigger kind `once {at, timeZone}`: `at` is an instant (ISO 8601 with an
offset, stored in UTC), `timeZone` the IANA zone the person thinks in (for wording only). Made on the Schedules page
it must be at least 60 s and at most 366 days ahead. Its one slot is `o:<iso>`, claimed as a cron slot is (the same
compare-and-set of `nextFireAt` and the firing's unique slot key), and the same store write disables it
(`statusReason: ran_once`), so it fires exactly once whatever the number of firers. A fired one-off is listed with
its run and never fires again; resume and run now refuse it (`SCHEDULE_RAN_ONCE`), delete removes it. A slot missed
while PipesHub was down fires on the next tick if it is at most 24 h late (a joke 20 minutes late is still the joke
asked for); later than that its firing is recorded skipped (`once_too_late`), the schedule is disabled and the
person is told as for any refused firing. A one-off counts against the same per-person and per-org schedule limits;
`catchUp` and coalescing do not apply.

**D2. The agent proposes, the person approves the exact schedule.** Harness runs (the loop and every engine, through
the Tool Gateway) get one platform tool, `schedule_create` (`pipeshub@schedules/create`), offered only when the org's
harness predicate and Labs `ENABLE_SCHEDULED_RUNS` are both on, and not to delegated child runs or to runs a schedule
fired. Its arguments are the schedule as the person will see it: `name`, `ask` (what each run is asked, written so it
stands alone), `when` (`{kind: once, at, timeZone}` or `{kind: cron, cron, timeZone}`) and an optional `notify`. Its
effect is `write`, and it asks the person every time: a conversation-wide "allow" and the org's rules can never let
it through without its own card (layers only narrow). A call that cannot succeed (a bad zone, a cron under 5
minutes) is refused to the model before it reaches a card. Its description carries the two earlier designs' rules:
make one only when the person asked for something later or repeated, never invent a time or repetition they did not
ask for, resolve "in 5 minutes" and "tomorrow at 9" from the run's current time and time zone (engines are now told
them too), and say back in plain words what will run and when.

**D3. Node creates it only on the person's decision for that call.** The worker does not hold the person's
credential, so it asks Node on the harness state API, `POST /api/v1/agent-harness/internal/runs/{runId}/schedules`
with the attempt token, sending the call's interaction id, the digest of the arguments the call was made with and
the arguments it runs with. Node creates the schedule only when the run's own journal shows that the interaction was
asked for this tool and these arguments (its `interaction.requested` binding digest, in the loop's and the engines'
forms) and that the run's own person approved it (`interaction.resolved`, not a system or grant decision); when the
person edited the call (AP-06), the arguments must be the edited ones (the decision's answer hash). Anything else is
refused `SCHEDULE_NOT_APPROVED` and nothing is made. The schedule is the run's person's, made through
`ScheduleService.create` with every check a create from the Schedules page makes (the Labs flag, rollout, quotas,
the 5-minute cron floor, budget, notify channels), with the person's authority re-read as a firing re-reads it. It
runs the chat's agent, agent version, model, chat mode, tools and locale, and records its origin (conversation, run,
interaction). Its id is derived from the org and the interaction, so one decision makes one schedule: a repeat
answers the same schedule (`created: false`). A one-off approved after its time (the person read "in 2 minutes"
three minutes later) is still made, and fires on the next tick.

This deliberately differs from ADR-WF-01 D7 (a question card and a confirm route the person calls). Both keep the
guarantee that matters: the person decides with their own authenticated request, and Node reads what was decided
from the run's journal, never from what the worker sends. The approval card is already on every surface that runs a
harness turn (classic chat, engines, Needs you, Allow all) and supports editing the call, so no new card, confirm
route or proposal store is needed.

**D4. The card says what will happen in plain words.** "Set up a schedule", the name, when ("Once, today at
3:47 PM", "Every weekday at 9:00 AM (America/Los_Angeles)"; a cron outside the common shapes is shown as written),
what each run is asked, and where to manage it. The wording is the app's, in the reader's language. The card is
chosen by the approval's tool id, so a connector tool that happens to be called `schedule_create` is not dressed as
one.

**D5. Where its runs answer.** Each firing starts its own conversation and tells the person on their notice
channels, as every schedule does (one conversation per firing, ADR-WF-01 D8). The tool's answer tells the model
where the result will appear and that the person manages it in Workspace → Schedules. Answering inside the chat
that asked is a later step: a turn joining a conversation the person may be typing in needs its own design.

**D6. The model makes, it does not manage.** No list, pause, resume or delete tool: as in loom, removing a schedule
is the person's act on the Schedules page, and a list is something a person reads, not a model's input.

## As built

- Node: `domain/schedules.ts` (`once`, `o:` slots, the lateness decision), `application/schedule-service.ts`,
  `application/schedule-firer.ts`, `application/chat-schedules.ts` (`ChatScheduleService`, the journal check),
  `transport/chat-schedule-controller.ts` and `transport/internal-routes.ts`, the three schedule stores (`claimCronSlot`
  disables a one-off in the same write), OpenAPI (`once`, `origin`, the internal route); error codes
  `SCHEDULE_NOT_APPROVED`, `SCHEDULE_RAN_ONCE`.
- Python: `app/harness/schedules.py` (the tool, its schema and checks, `NodeSchedules`, `ScheduleAsks`), wired in the
  loop's and the engines' policy chains; `is_scheduled_runs_on`; `RunTrigger.kind` `once`; the engine task's time
  line; `ToolContext` carries the approved interaction id and the call's digest.
- Frontend: the Schedules page and agent card read and make one-offs ("Once, …", "Ran once"), the approval card for
  the tool, a small cron wording module, ten locales.
- Tests: Node `sch01-schedule-from-chat.test.ts` and the schedule store contract; Python
  `tests/unit/harness/test_sch01_schedule_from_chat.py` and eval case C-05 ("Can you tell me a joke in 5
  minutes?"); frontend `sch01-*` suites.

## Deviations

- Approval is the existing approval card, not a question card with a confirm route (D3).
- A schedule deleted after it was made can be made again by a retry of the same approved call.

## Owner amendments

None.

## Status

**Accepted** (2026-11-26), decided from first principles, long term, UX-first.
