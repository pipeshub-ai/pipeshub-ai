# ADR-UI-01: What the background-agents interface reads: a person's runs, their chat list's state, a job's brief and its live timeline

- Gaps: G1 (runs and jobs across conversations, chat-list state), G4 (job brief, live job timeline), G7 (an agent's Runs tab, rides on G1) of the UI revamp plan (phase P2 "Background agents", decisions D1–D9 approved). G5 is ADR-GRANT-01 D8; G11 is ADR-OPS-01's "G11" amendment.
- Related: ADR-OWD-05 (durable run and session), ADR-OWD-13 (public API naming), ADR-OWD-19 (multi-participant sessions), ADR-RUN-01 (run outcome), ADR-SEC-02 (org binding), ADR-OPS-01 (the admins' Running now list).

## Context

The new interface shows background work across chats: a Jobs panel listing what is working, what needs the person and what finished; sidebar dots on each chat (working, needs you, finished and not seen yet); a job's Brief tab; and a job's Activity that feels live. The backend had none of it:

1. Runs were readable one at a time, inside their conversation (`GET …/conversations/{id}/runs/{runId}`), or org-wide for admins only (`GET /agent-harness/admin/runs`). Nothing listed a person's runs, so a job that finished after the person left told nobody.
2. The conversation list carried no run state, and nothing recorded when a person last opened a chat, so "finished, not seen yet" could not be said.
3. A job's goal, what it was sent and what it was kept from lived only in its pinned spec and input: internals the interface must not show.
4. The chat followed a job by reading its whole timeline every 3 seconds.

## Decision

**D1. One list of a person's runs: `GET /api/v1/agent-harness/runs`.** The caller's own runs and jobs (a job is a run another run handed work to: an engine or sub-agent child) across every conversation, newest first, filtered by state, agent, kind (`root`, `job`, `all`) and creation time, paged by an opaque cursor (`createdAt:runId`, base64url; the run id breaks a tie of creation time, so a page boundary never repeats or skips a run). The org and the person are always the token's: there is no parameter that names another person, and an admin's view of the org stays `/admin/runs`. An agent's Runs tab (G7) is the same list with `agentKey`; it lists the caller's runs of that agent, never other people's.

- **One meaning per state word** (`domain/run-lists.ts`, `runListState`): `working` (going, nobody is needed), `needs_you` (an open ask on the run, or an approval hold), `finished` (completed), `failed`, `stopped` (cancelled, by a person or an operator). An open ask wins over "running". The same reading is the stores' filter (`STATE_STATUSES` plus the open-asks test), held equal on all three stores by the shared contract (UIB1-01..06).
- **Stores** (`RunLists` port, implemented by the in-memory, MongoDB and PostgreSQL execution stores): `personRuns`, plus the two reads of D2. Indexes fit the query shape exactly: `{orgId, actorId, createdAt: -1, runId: -1}` and `{orgId, actorId, agentKey, createdAt: -1, runId: -1}` (Mongo migration `run-lists`; PostgreSQL `harness_0010`, built concurrently by `ensure_index`). A job's engine is read from the engine profile its admission pinned or, for a `code_task` child, its pinned executor; the list never fails on one unreadable run.

**D2. The chat list carries each conversation's run state; the person's "seen up to" mark lives on the conversation.** `GET /api/v1/conversations` and `GET /api/v1/agents/{agentKey}/conversations` add `harnessState` to each listed conversation: `activeRun` (anyone's), `runState` (`working`, `needs_you` or null), `needsYou` (an open ask on one of the caller's own runs or jobs there), `finishedUnseen` (one of the caller's runs ended after their mark, and nothing runs now) and `seenUpTo`. A list whose states cannot be read is answered as it was (logged `run.list_states_failed`), and nothing is added while the harness is off.

- **`PUT …/conversations/{id}/seen`** (chat and agent) moves the caller's mark to the server's time unless it is already later; the body names nothing, so a client cannot hide a later run with a future mark. The owner and each person the chat is shared with have their own mark; anyone else gets 404. Once the mark is set, the caller's unread finished-run notices of that conversation are read in their Inbox ([ADR-INBOX-01](ADR-INBOX-01-inbox-feed.md)), so the badge and the chat list agree.
- **The mark is stored on the conversation itself** (`chatSessions.harnessSeen.{userId}`, never selected with the session; PostgreSQL `conversation.chat_sessions.harness_seen`, `conversation_0004`), written without touching the session's own times, so the list's order does not move. Erasing a conversation erases its marks with it; no new store needs an erasure step. A conversation never marked has nothing unseen: the mark starts when the person first opens it, so the rollout does not light every old chat.
- Two store reads per list page, bounded by the page: the open runs of its conversations (`MAX_SESSION_OPEN_RUNS` = 1,000) by the runs-by-session index, and the caller's last ended run in each.

**D3. A job's brief: `GET …/runs/{runId}/brief`.** For the conversation's owner only (the same reader as the job's timeline); a run that is no job is not found. Built by one pure function (`domain/job-brief.ts`) from what the job's admission fixed, so it never changes after the job starts:

- `goal`: the task the parent gave it (null once the conversation's content was erased);
- `sent`: the files of the turn handed to an engine job (CTL-6), the sources the parent had read when it handed the work over (its pinned `inheritedLabel`: records and connector items; `sourcesComplete` false when some content cannot be named), what it may search (knowledge apps and collections), skills, and the earlier job it continues;
- `withheld`, each with a reason the interface words: the chat around its task (`task_only`, always), keys and sign-ins (`outside_sandbox`, every engine job, ADR-OWD-01), the parent's tools, knowledge, skills and websites it was not handed (`not_granted`, by name), files of the conversation it was not given (`not_handed_off`);
- `reach`: no website or the org's allowed websites, whether it works in a sandbox, its tools by app and name, and how many conversation grants it started with.

No spec, digest, policy layer or grant key reaches the brief (contract `job-brief.schema.json`, whose invalid cases include a leaked digest).

**D4. A job's own timeline is pushed on the run stream it already has.** `GET …/runs/{jobRunId}/stream` is the job's own AG-UI stream: the run stream service follows any run of the session, a job included, with the same frames, the same resumable ids (`Last-Event-ID`) and the same owner and shared audiences. The panel opens it instead of reading `…/runs/{jobRunId}/timeline` every 3 seconds, and reads the timeline once for what the stream does not carry (merge reports, budget). Chosen over adding child events to the parent's stream: a job's frames would repeat on every viewer of the conversation, and the parent's ids would have to name positions in two streams. The parent's stream keeps only its children's cards (UX3).

**D5. How a job's goal changed (UI-B5, UIP2-FU-4): `GET …/runs/{runId}/brief/history`.** For the conversation's owner only, a job only (as the brief). `startedAs` is the goal its parent gave it; `changes`, oldest first, are read, never stored: from the job's own journal, the messages added to it (`input.queued`) and the choices its person made when it asked first (each decided approval, the action as English and as `actionKey`, ADR-WF-01 D15); from the conversation's jobs, each later job that continues it (its pinned `engineCarry` names it): the coordinator re-planned the work into it (`replanned`, with that job's goal). Access allowed is not a change of goal: it is the brief's. Contract `job-brief-history.schema.json`.

**D6. One request's own view (UI-B5, UIP2-FU-4): `GET …/runs/{runId}/jobs`.** For the conversation's owner only: the request (its state and what was asked) and the jobs it handed out, oldest first, each with its title, state, engine and the earlier job it continues, and how many are back (`back` of `total`: finished, failed or stopped). Contract `request-jobs.schema.json`. Both reads take the conversation's latest 200 jobs from one store read (`RunLists.sessionJobs`, by the runs-by-session index on every store, no migration), with each job's engine and continued job read from its pinned spec.

**D7. Job titles on the person's list (UI-B5, UIP2-FU-1).** Each job row of `GET /agent-harness/runs` carries `title`, its goal's first line (spaces folded, at most 200 code points), so the Jobs panel needs no brief per row. The goal is opened from the run as stored (sealed, LCFIX) for the page's jobs only; one that cannot be opened lists without a title. The list is the caller's own, so the title is their own conversation's content.

### Alternatives rejected

- **A separate per-agent route** (`GET /agents/{agentKey}/runs`): the same query with one filter, and a second path to guard; the list's `agentKey` is the Runs tab.
- **A harness collection for seen marks:** a new store with its own erasure, principal-erasure and backup steps for a timestamp; the conversation already goes where the mark must go.
- **Client-supplied seen times:** a future mark would hide later runs; the server's clock is the only writer.
- **Child events on the parent's stream:** see D4.

## As built

- Node: `domain/run-lists.ts`, `ports/run-lists.ts`, `application/run-lists.ts` (`RunListService`), `domain/job-brief.ts`, `application/job-briefs.ts`, `transport/run-list-routes.ts` (`createAgentHarnessRunsRouter`, mounted at `/api/v1/agent-harness/runs` in `app.ts`), `transport/background-agent-controller.ts` and four session routes in `transport/session-routes.ts`, `transport/conversation-run-states.ts` (on both list routes in `enterprise_search/routes/es.routes.ts`), `binders/run-lists.ts`; the three execution stores; the conversation repositories' `markHarnessSeen` / `harnessSeenMarks`; `libs/repositories/agent-harness/conversation-seen`.
- Contracts: `contracts/harness/ext/pipeshub/api/person-runs.schema.json`, `contracts/harness/ext/pipeshub/api/conversation-run-state.schema.json`, `contracts/harness/ext/pipeshub/api/conversation-seen.schema.json` and `contracts/harness/ext/pipeshub/api/job-brief.schema.json`, generated for Node, Python and the frontend. Every route in `pipeshub-openapi.yaml`; QA-26 rows for each.
- Migrations: Mongo `run-lists` (two run indexes); PostgreSQL `harness_0010_run_lists` (two indexes) and `conversation_0004_harness_seen` (one nullable column), with the security surface snapshot.
- Tests: N `uib1-run-lists.test.ts`, `uib1-job-brief.test.ts`, `uib1-routes.test.ts`, `projection/uib1-job-stream.http.test.ts`; the shared contracts `execution-store-contract/run-lists.ts` (UIB1-01..06) and `child-run-contract.ts` UIB1-04 on all three stores; `conversation-seen-contract.ts` (SEEN-01..03) in memory, `conversation-seen.mongo.itest.ts` and `conversation-seen.pg.itest.ts`.

- UI-B5 (D5–D7): `projection/job-brief-history.ts`, `JobBriefService.history` / `requestJobs`, `RunTimelineService.goalChanges`, `RunLists.sessionJobs` on the three execution stores (contract `child-run-contract.ts` UIB5-S1), `domain/run-lists.ts` (`ListedJob`, `listedCarry`), `domain/job-brief.ts` (`jobTitle`), `RunListService` titles; contracts `job-brief-history.schema.json`, `request-jobs.schema.json`, `person-runs.schema.json` `title`; two routes on each conversation router in `pipeshub-openapi.yaml` and QA-26; tests `uib5-job-history.test.ts`, `uib5-routes.http.test.ts`.

## Deviations

- The agent conversation list decorates its `conversations` only, not `sharedWithMeConversations`.
- `needsYou` on the chat list counts the caller's own runs and jobs; a shared chat whose ask belongs to someone else reads `working` for the caller (D8: the person who asked approves).
- A completed root run that asked a question (`outcome: needs_input`) lists as `finished` with its outcome; the question inbox (G-13) is where an unanswered one waits.
- The marks are compared with Node's clock, the runs' end times with the database's; a skew of a few seconds can at worst show a run that ended in that window as unseen once.

## Owner amendments

None.

## Status

**Accepted** (2026-11-09), decided by UI-B1 on the plan's approved decisions D1–D9.
