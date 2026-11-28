# ADR-SCH-03: Managing schedules from chat

- Amends: ADR-SCH-01 D6 ("the model makes, it does not manage"), which this replaces.
- Builds on: ADR-SCH-01 (a schedule asked for in chat; one-off schedules; the journal-bound approval), ADR-SCH-02
  (schedules without the harness; classic chat's approval path).
- Related: ADR-WF-01 D3 (an edit checked as a create checks it; set aside with the new interface), main #4313 (writes
  that act for the person ask), ADR-SEC-02 (org binding).

## Context

A person said "tell me a joke after 2 mins", then "can you make it an animal joke?". The agent could not: it could
create a schedule and nothing else. ADR-SCH-01 D6 followed loom, where changing a schedule is an operator's act. In a
chat it is the most natural next sentence. What people say about a reminder or a recurring task, once it exists:

| They say | They mean |
| --- | --- |
| "make it an animal joke", "also include the open bugs" | change what it asks |
| "make it 10 minutes instead", "move it to 9 am", "weekdays, not every day" | change when |
| "send it to Slack too", "rename it" | change how it tells them, its name |
| "pause it until Monday", "resume it", "skip tomorrow's" | pause, resume, skip one |
| "run it now" | run now |
| "cancel that reminder", "stop the Monday summary" | delete |
| "what reminders do I have?", "when does the standup one run next?" | find and read |
| "that one", "the reminder I just set" | refer to one this chat knows |

Node has create, get, list, delete, pause, resume, run now and an agent-version move. It has no edit.

## Decision

**D1. One schedules toolset, four tools, on both chat paths.** `schedule_create` (as built), `schedule_find`,
`schedule_update` and `schedule_delete`, offered together wherever `schedule_create` is (ADR-SCH-02: the org's Labs
**Scheduled runs**, a person's chat, never a scheduled firing or a delegated child). One shared module holds their
argument contracts, their checks before any card, and their wording, for the harness tool source and classic chat
alike.

**D2. Finding is a read of the person's own schedules.** `schedule_find {query?, status?, madeHere?}` answers the
caller's own schedules only (the same owner rule as `GET /schedules`), at most 20, each as id, name, what it asks
(shortened), when in plain words, the next run, status, and whether this chat made it. Without a query it lists them,
the ones this chat made first, then the next to run. It changes nothing, so it asks nothing. An admin's wider reach
on the Schedules page is not offered to the model.

**D3. "That one" is resolved, never guessed.** Every schedule records the conversation that made it (ADR-SCH-01's
origin; a schedule made on the Schedules page has none), and a change made from a chat records that chat as the last
place it was changed from. `schedule_update` and `schedule_delete` take a schedule id. The tool descriptions tell the
model to find first, and when more than one schedule could be meant to ask the person which. A write never acts on a
name.

**D4. `schedule_update` is one patch with plain-word effects.** `schedule_update {id, name?, ask?, when?, notify?,
action?}`: `action` is one of `pause`, `resume`, `skip_next`, `run_now`, and is not combined with field changes in one
call. Node gains `PATCH /api/v1/agent-harness/schedules/{id}` for the fields, checked exactly as a create checks them
(the trigger rules, the 5-minute cron floor, the one-off's window, quotas, notice channels), and `POST …/{id}/skip`
for skip-next. Rules that keep schedules predictable:
- A one-off that already ran or was skipped cannot be edited (`SCHEDULE_RAN_ONCE`): the answer says to make a new one.
- Moving a one-off's time keeps it a one-off with one slot, claimed once.
- Changing a recurring schedule's time takes effect from its next slot; a slot already claimed runs as it was.
- Changing what it asks or its notices takes effect from the next firing; a firing already running is not changed.
- Pause, resume and run now keep their rules (resume skips the paused slots; run now on an unfired one-off takes its
  one slot).
- Only the owner edits. An admin may pause, resume or delete on the Schedules page, as today, not from the model.

**D5. Every write asks, and the card shows the change.** `schedule_update` and `schedule_delete` ask the person every
time, on both paths, never remembered and never allowed by a rule (as `schedule_create`). The card says what changes:
"Change *Joke in two minutes*: What it asks: *a short joke* → *a short animal joke*", "Move *Standup summary*: every
weekday at 9:00 AM → every weekday at 8:30 AM", "Pause *Standup summary*", "Delete *Joke in two minutes*". Node gives
the card the schedule's current values: the tool reads the schedule before asking, and the approval is bound to the
call's arguments, which include the id. If the schedule changed between the card and the approval, the write is
refused (`SCHEDULE_CHANGED`, the schedule's revision) and the model reads it again.

**D6. The approval is bound as for a create.** On the harness the internal routes act only on the person's journaled
approval of that call (ADR-SCH-01 D3, generalized: `POST /internal/runs/{runId}/schedules/{id}/update | delete`). On
classic chat the write runs inside the person's own request that approved it, with that request's credential, through
the public routes (ADR-SCH-02 D5).

**D7. What the tools answer.** Each answer says what is now true in plain words ("*Joke in two minutes* will now tell
a short animal joke, today at 3:47 PM", "Paused *Standup summary*; it won't run until you resume it"), and what the
person can do next. A refusal says why in the person's terms and tells the model not to retry the same call.

**D8. The schedules toolset is loaded when it is needed, not before.** The four tools are one first-party toolset,
`schedules`, described in one line ("set up, find, change, pause and delete reminders and recurring runs"). It is
*on demand*, a disclosure class between *essential* (always listed in full) and the threshold-lazy toolsets (listed in
full until a grant passes the size threshold): whatever the grant's size, the model sees `schedules` as that one line in
the toolset overview and loads its tools through the existing discovery (`list_toolsets`, `fetch_tools`,
`search_tools`, `call_tool`; on engines the gateway's `search_tools`/`call_tool`). This follows the practice of keeping
only the few most-used tools in full and deferring the rest by group (Anthropic's tool search guidance:
`defer_loading` on a group, three to five tools kept loaded), on PipesHub's own mechanism:
- The run's tool list is fixed for the session, as every lazy run's is (R4), so prompt caching holds; when a grant would
  otherwise be all eager, the discovery tools are listed from the first turn because an on-demand toolset exists.
- An on-demand toolset is not counted toward the lazy threshold, so adding it never changes how a person's own tools
  are disclosed (ADR-OWD-07 D-DISC-1).
- The goal-based preloading (`tool_preloading`) unlocks `schedules` before the first turn when the request clearly
  asks for something later or repeated, so "remind me in 5 minutes" costs no discovery round trip; a weaker match gets
  the one-line pointer.
- A tool reached through `call_tool` is unwrapped to its own id before grant, checks and approval, so "asks every time"
  holds however it was loaded.
- The tools share the `schedule_` prefix and their descriptions carry the words people use ("remind", "later",
  "every Monday", "cancel"), so a search finds the group.

## As built

- Contracts: `contracts/harness/ext/pipeshub/api/schedule-update-arguments.schema.json`,
  `contracts/harness/ext/pipeshub/api/schedule-delete-arguments.schema.json`,
  `contracts/harness/ext/pipeshub/api/schedule-find-arguments.schema.json` and the shared
  vectors `contracts/harness/vectors/schedule-manage-arguments.json` (the update rule the generated models cannot say,
  fields or one action, is in code and in the vectors); error `SCHEDULE_CHANGED`.
- Node: `PATCH /schedules/{id}` (every create check, D4's rules), `POST …/{id}/skip`, revision-checked pause, resume
  and delete (inside each store's own write: in-memory, Mongo, PostgreSQL), list filters (`query`, `status`,
  `conversationId`, `madeHere`), `revision`, `lastChangedFrom` and `skipNextAt` on the schedule view, and the harness's
  journal-bound `/internal/runs/{runId}/schedules` find, read, update and delete.
- Python: one shared module (`app/schedules/arguments.py`) for the four tools' contracts, checks and wording; the
  harness tools (`app/harness/schedules.py`) and classic chat's (`app/agents/agent_loop/schedule_tool.py`), each write
  asking every time with the schedule's current values on the card; the `schedules` toolset on demand (D8: `on_demand`
  groups in `harness_core/tools/registry.py`, the lazy planner, the durable disclosure and the engine disclosure, and
  the preloading cues).
- Frontend: the change and delete cards on both paths from one formatter; the Schedules page's drawer for New
  schedule and Edit (revision-checked, unsaved changes kept), "last changed from a chat" and "next run skipped".
- Tests: Node `sch03-managing-schedules.test.ts` and the schedule store contract; Python
  `tests/unit/agents/agent_loop/test_sch03_managing_schedules.py` and `test_sch03_schedules_on_demand.py`; frontend
  `sch03-*` suites.

## Deviations

- The preloading cues: a keyword index cannot score "after 2 mins" against tool text, so an on-demand group carries
  its own cue and hint patterns, which unlock it or point at it before the first turn.
- Delete does not record the chat it came from (the schedule is gone; a tombstone keeps only its origin).
- D5's current values on the harness: the durable loop journals an ask's reason as `interaction.explained`, which has
  no field for them (an unknown field is refused and the run fails). The harness card reads the schedule itself
  (`GET /agent-harness/schedules/{id}`, as the person) when its ask carries none; classic chat's card is sent them.
  A schedule that cannot be read leaves the card listing the call's arguments.
- D6 through `call_tool` (D8): the durable loop journals the model's call, the `call_tool` wrapper, so the approval is
  bound to the wrapper's digest while the tool runs with the arguments inside it. The tool's context carries the name
  the wrapper named (`ToolContext.called_as`) and the internal route's body carries it (`calledAs`). Node recomputes
  the wrapper's digest from the arguments it is sent, `{name: calledAs, arguments}`, and accepts the call only when
  that, or the arguments' own digest, is the journaled one. Engines are unchanged: their gateway unwraps `call_tool`
  before the digest is taken.
- A schedule made in chat keeps that chat's coding engine (`AgentSchedule.codingEngine`, an engine kind or `none`).
  Each firing opens a new chat, which otherwise took its owner's default: a chat that chose no engine made a schedule
  whose run handed the ask to the owner's personal engine. The firing names it as a new chat's first message does
  (CMDUI2), so admission checks it against the org's switches and the person's groups at every firing, and an engine
  since turned off falls back to the default. A failed read of the chat's choice makes the schedule on the default.
  Kept in the schedule document on both stores; no migration (PostgreSQL keeps the document as JSON).

## Owner amendments

- 2026-11-27: tools are loaded progressively; the model sees the toolset and what it can do, and loads its tools
  lazily (D8).

## Status

**Proposed** (2026-11-27), owner direction: "the agent couldn't edit the existing reminder… design this properly";
decided from first principles, long term, UX-first.
