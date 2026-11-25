# ADR-INBOX-01: One Inbox per person, finished-run notices, and one search for the palette

- Supersedes: nothing. The four separate lists the Inbox merges (notifications, the approvals waiting on the person and the questions their runs asked them, `GET /skills/candidates/pending`, the memory review lists) keep their services and rules; the Inbox reads them.
- Related: UI revamp plan P3 (gaps G2 and G9; decisions D1-D9), ADR-OWD-09 (policy and approvals), ADR-OWD-19 (multi-participant sessions), ADR-UI-01 (UI-B1: runs list, chat state and conversation seen marks), ADR-SEC-02 (org binding), design.md §6A.16, §6A.17, §6A.34, §6B.23-25.

## Context

The new interface's Inbox is "one place to catch up; a run finishing after you leave tells you", and its ⌘K palette is "one door that finds anything". The backend had neither:

- What waits on a person was in four lists with four shapes: main's notifications (socket push, read/archive), pending approvals and questions, skill candidates (admin review in the skills service), memory reviews. None sorted asks before news, none had a stable kind the UI could name a row by, and none could be filtered, muted or paused.
- A person's own chat turn that ended after they left told nobody. Schedules told their actor (TR-07); interactive runs did not.
- Search lived in five places (conversation search, the knowledge hub's record search, agents, schedules, artifacts), each with its own matching; none forgave a typo.

## Decision

**D1. The Inbox is a read-time merge, not a copy.** Asks (approvals the person may decide, from the runs the approvals queue's reach covers (`ApprovalQueueReach`, by default the person's own), questions their runs asked them, memory reviews, and for an org admin skill proposals) are read from their own services on every read, so an ask decided anywhere leaves the Inbox at once and nothing goes stale. Notices are main's stored notifications. The feed answers asks first (newest first), then notices newest first; asks are on the first page only, notices page by a keyset cursor. A source that cannot answer is named in `partial`; nothing of it is guessed.

**D2. Every item has one shape.** A stable `id` (`n:` notice, `a:` approval, `q:` question, `s:` skill proposal, `m:` memory), a `kind` from one enum (`INBOX_ITEM_KINDS`, the contract's), a `severity`, `waiting` (an ask), `unread`, `archived`, a plain `title` and `description`, the time `at` (epoch ms), a `pointer` (conversation, schedule, skill candidate, memory or an in-app link, by the ids the routes take) and an in-app `link`. A notice's kind comes from the type its writer gave it (connector news from its origin service), so new writers need no Inbox change to be listed, only a type to be named. Titles name what the row is about (the chat, the workflow); the description says how it went (design §6B.25).

**D3. Filters.** `all`; `unread` (every ask and unread notices); `mentions`; `workflows` (a workflow's runs, failures and waits); `shared`; `connectors`; `skills` (skill proposals and memory reviews: what agents propose to learn). A filter main's list cannot apply (a kind) is applied while reading forward, at most five reads of 100 per page, and the page hands back a cursor to carry on, so a sparse filter answers quickly.

**D4. Actions on notices.** `read`, `unread`, `archive`, `unarchive`, `restore` (every archived notice back), each on the caller's ids, on `all`, or (read only) on one conversation's finished-run notices when they open it. An id of anyone else's matches nothing. Asks have no read state: deciding removes them (design §6B.24).

**D5. Mute.** A person mutes a workflow (a schedule they act as or, as an org admin, administer). A muted workflow's successful runs are not told on any channel; its failures and its asks still are: a workflow waiting on someone nobody hears about waits forever. At most 200 stay muted, the oldest dropping first.

**D6. Pause.** For an hour, until 09:00 tomorrow on the person's own clock (their IANA zone; UTC when unknown), or until resumed. While paused, notices still land in the Inbox (it is the record) but are not pushed, leave by no other channel, and the badge counts asks only. **Asks never pause**: approvals and questions are cards that wait on the person, so their pushes, emails and Slack links go as before.

**D7. Counts are live.** `unread`, `waiting` and `badge` (asks always; notices unless paused) are pushed as `inboxCount` on the existing notification socket after every Inbox action, a pause, and a finished-run notice; `newNotification` still announces each stored notice. A push after a finished-run notice counts the person as their role on the server says (`OrgAdminCheck`), never as a caller says; it carries no credential, so a source another service decides with the person's own (skill proposals) is counted on the read only. A push is a hint; the read is the record.

### Finished runs

**D8. The rule (from first principles: tell a person what they would otherwise not learn, once, and nothing they caused or already see).** When a run ends, Node writes a "finished" or "couldn't finish" notice for its owner when all of these hold:

1. it is a root run (a job's end is told by its parent's answer);
2. it is the person's own interactive run: not a workflow's firing (TR-07 tells those), not a service credential's (a Slack user got the answer in Slack);
3. it ended in a way the person did not cause and is not already asked about: `completed` with an answer ("Finished", or "Finished early" when a limit cut it short), or `failed` ("Couldn't finish"). `cancelled` is not told (the person or an admin stopped it; an admin's stop reason belongs to G11), and a run that ended by asking a question is not told (the question is already an ask in the Inbox);
4. the owner was **not watching its conversation when it ended** (D9);
5. no other replica told it already (a claim per run, first insert wins).

The notice lands in the Inbox (pushed unless paused). It also goes by email and by the agent's Slack bot where the person opted in (`runNotices`, off by default) **and** the org allows that channel (the schedules' `triggers.notifyChannels`, one setting for both), never while paused. The channels are the schedule notifier's own (TR-07: the mail service's template and the agent's bot; a plain chat has no bot, so Slack is an agent chat's only). Opening the conversation reads its finished-run notices (`PATCH …/notices {action: read, conversationId}`; UI-B1's seen mark calls the same once it lands).

**D9. Watching.** An open stream of a conversation (`GET …/runs/:runId/stream`, or a chat turn's own stream) holds a lease on it, renewed at half its length (60 s) while open and given back when it closes, in the Inbox store so every Node replica sees it (a run can end on a replica other than the one holding the stream). A lease that lapses (a replica died) only means a notice may be written for a run the person did watch end; never the other way round. Someone else watching a shared conversation is not the owner watching it.

### Palette

**D10. One permission-filtered search.** `GET …/palette/search?q=` asks every kind's own service with the person's own credential, in parallel: chats (their own, agent chats, shared with them, archived: the conversation repository's own visibility rules), documents and records (the knowledge hub's permission-filtered search, the All Records page's), agents (the query service's list), workflows (the schedule service's "mine"), artifacts (the gallery's search) and commands (a registry; admin ones for admins). A restricted record is never a candidate, so no total, page or "hidden" line can betray it (design §6A.34 "Permissions first").

**D11. One matcher.** The design's: title at double weight, content and hidden tags; half the meaningful words must land; one typo or swapped pair in a word of four letters or more still matches; near misses go when anything matched as typed. A searched source (documents, artifacts) matches on its own side and its total is the count; the palette only ranks and highlights. Groups come in one order with caps of 4 chats, 5 documents and 3 of each other kind, each with its total and a "Show all" cursor (`kind` + `cursor` pages that kind). `askFirst` follows the design's question rule.

**D12. Fast enough to type.** Lists that do not change between keystrokes (a person's chat window, agents, workflows) are kept per person for 20 s; a source slower than 400 ms is left out of the answer (`partial`) and asked alone by the client. Budget p50 at most 150 ms on data the size of a busy test org, measured by `palette-latency.test.ts`.

## As built

- Node domain: `domain/inbox.ts` (kinds, filters, order, pause, counts, cursors, the finished-run rule and its words), `domain/palette.ts`, `domain/palette-match.ts`.
- Node application: `application/inbox-feed.ts` (D1-D7), `application/inbox-sources.ts` (approvals, questions, memories, skills), `application/inbox-notice-policy.ts` (D5, D6 for workflows), `application/run-end-notices.ts` (D8, D9), `application/palette.ts` and `application/palette-sources.ts` (D10-D12); binder `binders/inbox.ts`.
- Stores: `InboxStore` (settings, run-notice claims, watch leases) in memory, on MongoDB (`agentHarnessInboxPreferences`, `agentHarnessRunEndNotices` with a 30-day TTL, `agentHarnessRunWatchers` with a TTL on the lease, migration `inbox`) and on PostgreSQL (`harness.inbox_preferences`, `run_end_notices`, `run_watchers`, `harness_0011`), one shared contract suite. Notices through main's notification repository on either store.
- Routes: `GET /api/v1/agent-harness/inbox`, `GET …/inbox/count`, `PATCH …/inbox/notices`, `GET` and `PATCH …/inbox/preferences`, `GET /api/v1/agent-harness/palette/search`; OpenAPI; QA-26 rows; contracts in `contracts/harness/ext/pipeshub/api/` (`inbox-feed`, `inbox-item`, `inbox-counts`, `inbox-notices-result`, `inbox-preferences`, `palette-results`, `palette-group`, `palette-page`).
- Channels: `HarnessScheduleNotifier.runFinished` (Inbox, email, Slack), `quiet` notices; `SocketInboxCountPublisher`; `QuerySkillProposals`; the palette's chat, record, artifact and agent sources (`enterprise_search/adapters/harness-palette-sources.ts`).

## Deviations

- Mentions: the kind, its filter and its row exist (`chatMention`); no writer yet. G10 (shared chat presence and @-mentions of people) writes them.
- A cancelled run is never told here; once G11 keeps who stopped a run and why, a stop by someone other than the owner should be told (`run_not_finished`, "An admin stopped this").
- An MCP client's run is an interactive run the person may never watch in the app; it is told like any other. If that proves noisy, the run document needs its surface (today only the spec pins it).
- Email for a finished chat reuses the scheduled-run template (`ScheduledRunEnded`) with the chat's title in the schedule's place; a chat's own template is a follow-up.

## Owner amendments

None yet. D1-D9 are the plan's; the rule in D8 is this package's, decided from first principles as the plan asked.

Follow-ups, UI-B4, 2026-11-09 (A3: no server-side skill token):

**A1. A typed subject on every item (UIP3-FU-1).** `InboxItem` gains `subjectVersion: 1` and `subject` (`inbox-item.schema.json#/$defs/InboxSubject`): what the row is about by name, never in sentences, so every locale words it without parsing the server's English. Each kind carries its own fields: approval `action {name, app}` (the tool's display name, else its name; the app is the tool id's toolset or MCP server) or `connect`; question `question`; skill_proposal `skill`; memory_review `memory`; run_finished and run_not_finished `chat` and `outcome` (`finished`, `cut_short`, `not_finished`); workflow kinds `workflow` and `outcome` (`ran`, `failed`, `refused` with `reason`, `waiting` with `deadlineAt`); share `sharedBy` and `chat`; mention `chat`; connector and system `source` (their words stay their writer's English). Notices take it from the payload their writer stores: the schedule notifier now stores `scheduleName`, the finished-run notice `chatTitle`; a notice written before has none of the names and the client falls back to `title`, which stays the English fallback. A client that knows a lower `subjectVersion` words the row from `title`. Test: every kind carries its subject (IBX-S1).

**A2. Deleting archived notices (UIP3-FU-2).** `POST /api/v1/agent-harness/inbox/notices/delete {ids} | {all: true}` deletes the caller's archived notices only (soft delete in main's notification repository): the ids are checked against the person's own archive, never trusted, so a notice not archived or not theirs is left alone; `all` takes up to 500 a call. Answers the deleted item ids and the counts, pushed when anything went. POST, not DELETE with a body, which some proxies drop.

**A3. A push that cannot count everything says so (FIXMS1-FU-1).** A count push after a run ends is made without the person's credential, and the skills service answers only with it (and no server-side skill token is minted, by decision). A source that counts only with the person's own credential declares it (`credentialOnly`, today skill proposals); a push from `announce` then carries `recount: true` and the client answers it by reading `GET …/inbox/count` with its own session instead of taking the pushed numbers. A read never carries it. Skill proposals now wait on whoever may decide them (the learning run's person, or an admin; the skills service decides, UI-B4).

## Status

**Accepted** 2026-11-09 (UI-B2, P3), within the owner-approved plan's D1-D9.

**Palette set aside** (2026-11-10, owner: the new interface waits). UI-ASIDE removed the ⌘K palette API ("Palette"
above) from `harness/v2-mainsync`; it lives on `harness/ui-revamp`. The Inbox feed, counts, typed subject and delete
stay: background jobs and the classic needs-you inbox build on them.
