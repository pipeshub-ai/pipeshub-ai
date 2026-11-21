# ADR-INBOX-02: An ask is counted live, worded once, and a question waits for its chat

- Supersedes: ADR-INBOX-01 D7 in part (when counts are pushed). Everything else in ADR-INBOX-01 stands.
- Related: classic jobs plan (`classic-jobs/PLAN.md`) D7, D10 and §4.4; CJ-5 findings F1, F2 and F4; QA-CLASSIC-FU-2 and FU-4; UXINT-3 (the approval card's words).

## Context

CJ-5's specs and the QA-CLASSIC pass found three faults in Needs you:

- **The count was not live (F1).** ADR-INBOX-01 D7 pushes `inboxCount` after an Inbox action, a pause and a finished-run notice. An ask is none of these: it is raised by a worker's commit and settled by a decision, an answer or the run's end. So a job that asked moved the server's `waiting` to 1 while every open page showed 0 until it reloaded.
- **Two wordings of one ask (F4, FU-2).** The chat's approval card words a call from its tool id (`interactionTitle`: "Create ticket in E2Esaas?"). The Inbox subject carried only the display name and an app split from the id, so Needs you said "Approve: E2Esaas · create_ticket".
- **What a question of an ended run is (FU-4).** A question asked as a turn's last step stayed "waiting" after its run completed, and cancelling the chat did not clear it.

## Decision

**D1. Asks are counted from the runs that hold them.** `runAsksKey(run)` names what a run asks of its person: its open interactions while a decision can still reach them (`DECIDABLE_RUN_STATUSES`), and, for a root run, the question it ended on (`completed` with `needs_input`). `ExecutionService` compares the key before and after each command (worker commit, control, sweep, failed run). When the key changes it tells its `onRunAsksChanged` listeners. A replayed receipt is not told again. The Inbox binder listens and announces the run actor's counts. Admission tells the Inbox (`questionAnswered(orgId, askingRunId)`) when a new turn answers a question, since that closes the question without any command on the asking run. The push goes to the asking run's actor, read from the run, since the sender need not be the person asked. A queued next message (SS2) that drains after its chat ended on a question tells it the same way (readmission reads the run the message answers as a fresh answer's admission does, so the drained run is held to that run's gates and cap (WP-64, FUQUICK-FU-1), and `NextRunService` tells the asker from the answer it admitted). This covers what D7 missed: an engine job that asks while it keeps running, a loop run that suspends, a decision, a cancel or sweep that closes open asks, and a question raised or answered.

**D2. Announcements for one person do not overlap.** If an announcement is asked for while one is counting, one more count runs after it. A burst of commits therefore costs at most two counts, and the last push is never older than the last change. A push is still a hint, and the read is the record. The push goes to the run's actor and to everyone whose approvals queue lists the run (FU-QUICK): the people the bound `ApprovalQueueReach` names through its optional `audience`, never the actor twice; anyone not told sees it on their next read. With the default reach (own runs only) no other queue lists the run, so nobody else is read or pushed. The reach is read at each change, since an edition binds it after the Inbox.

**D3. One wording for an ask.** `subject.action` carries `tool` (the tool's id) and `destination` (where the call sends, masked as the card shows it). The change is additive and optional, so `subjectVersion` stays 1. The client words the row with the approval card's own `interactionTitle`, so Needs you and the chat cannot drift, and every locale gets the card's translated words. A server from before this change sends no `tool`, and the client falls back to "Approve: {name}".

**D4. A question waits for its chat, not for its run.** Ending the run is how a turn asks a question: the answer is the person's next message (ADR-INBOX-01 D8.3, G-13). So a question does not close when its run ends. The chat's card still takes the answer, and Needs you agrees with it. The question leaves Needs you, and the push says so at once, when its chat moves on: it is answered, skipped ("Skip" sends "[No preference]"), or the person sends any other message. It also leaves when the person can no longer open the chat. Cancel has no run to stop, so it does not clear the question. This is not the case for an approval or any other interaction whose run ended before anyone decided: that ask closes with its run, because a decision can no longer reach it, and D1 now pushes the change.

**D5. Every sidebar row is marked by one rule (D10).** The row of the chat this tab is streaming (`GeneratingTitleItem`) and every listed row use `sidebarDot`. It merges the server's `harnessState` with this tab's own knowledge, and that knowledge now includes an ask the tab's stream holds open (`slotWaitingCount`). So the streaming row shows Needs you over its spinner, even before the list has the chat.

## As built

- Each change to a run's asks costs one count for its actor, coalesced per person, off the commit's path.
- Tests: Node `cjfixa-inbox-live.test.ts` (CJA-1..6), `uib4-inbox.test.ts` (the subject's `tool` and `destination`); frontend `inbox.test.ts` (the card's words in all ten locales), `cj1-data-layer.test.ts` and `chat-section-element.test.tsx` (D5). CJ-5's "Needs you is live", "D10" and CJ-E2 hold these as hard checks.
- Each change to a run's asks under an edition's reach also costs one directory read, two standing reads per candidate and one count per reached person (at most 50), off the commit's path.
- The two gaps first recorded here (approvers not pushed; a drained next message answering a question without a push) are closed by FU-QUICK: Node `fuquick-inbox-push.test.ts` (FQI-1..5).

## Deviations

None.

## Owner amendments

None: decided from first principles, long term, UX-first.

## Status

**Accepted** 2026-11-10.
