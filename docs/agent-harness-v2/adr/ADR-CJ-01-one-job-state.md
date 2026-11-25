# ADR-CJ-01: A job's state lives once, keyed by its run, and a shared viewer's live stream names its jobs

- Package: CJFIX-B (classic jobs, PLAN D2, D11, D13, D14, D15).
- Related: ADR-UI-01 D4 (a job's own stream), ADR-OWD-19 (multi-participant sessions).
- Findings it answers: CJ-5 F3, F6, F8, F9, F10 (STATUS "CJ-5 evidence", phase 2).

## Context

A background job is shown in several places at once: its card in the streaming answer, the same card again once
the answer settles (a new component), its inline steps, its panel's header, Activity and footer. CJ-2 and CJ-3
kept part of what they knew of the job inside each component:

1. **Timeline reads.** The card read the job's timeline once it ended; the panel read it on open and again when its
   activity said "finished"; the panel's engine steps read it on open and again on each change of the activity.
   An engine job's timeline was read four times in CJ-E1 (F8); D13's budget is one read after it ends.
2. **The Stop's outcome.** The card held "It had already finished" (a 409) in its own state, so the card drawn again
   when the answer settled lost it (F9).
3. **Status before the job's stream spoke.** The chat's `sub_agent` part says only that the job was handed out, so
   the card guessed Working, then the job's first snapshot said queued (Starting), then it started (F6).
4. **A shared viewer.** The turn's stream (audience `shared`) sent none of a child's lifecycle frames, though the
   viewer's saved answer holds the child's `sub_agent` part, so the viewer saw "Thinking…" and no card until the
   answer was saved (F3). The card's settle read then asked for the owner-only timeline and got 404.
5. **Names.** A loop sub-agent's part carried only its role id, so its card read "pipeshub-subagent-general" and
   its panel "Background job" (F10), while the Jobs page names it by its goal.

## Decision

**D1. What more than one place shows of a job lives in the jobs store, keyed by its run id** (`frontend/lib/harness-jobs/jobs-store.ts`): its stream (as before), its timeline and how a Stop of it went. No component keeps a copy.

- `loadJobTimeline(session, runId, { ended, force? })` is the one way a job's timeline is read: at most one read while it runs and one once it ended, whoever asks, never two at once. A read that finds the run ended counts as the ended read. `force` reads again after something changed it (a decision on one of its asks). `useJobTimeline` is the hook over it; the card's settle read (`readSettled`), its inline steps (`ChildRunPanel`), the job panel and the panel's footer use it.
- An engine job's panel steps come from the job's own stream frames, the same ones its card draws, not from its timeline.
- `stopJob(session, runId)` records `sending`, `sent`, `gone` (409) or `failed` for the job; the card and the panel header read it.

**D2. A job's status only moves forward.** Before its own stream has said anything, a handed-out job with no step reads Starting; one with steps reads Working. A snapshot can move a queued job to running and never back. The chat's part stays the authority on "it ended".

**D3. Only a follower who may read the timeline triggers the settle read.** `followJob(session, runId, { settle })`: the card and panel pass `settle: owner`; a shared viewer follows the stream (D14) and reads nothing else. Likewise the chat's activity run (`activityRun`) is none in someone else's chat, though the viewer follows the owner's run live, and the saved-undo rows read the workspace's versions only for its owner. The page's agent read stays: it is how a viewer learns whether they may open the chat's agent (a quiet 404 when not, no button behind it); saying that without asking needs the conversation read to carry it (CJFIX-B-FU-1).

**D4. A shared viewer's live stream names the children its saved answer will hold** (Node `RunProjector.child`). For audience `shared` the projector emits the child's `RUN_STARTED` and its `RUN_FINISHED` / `RUN_ERROR`, the same facts the saved `sub_agent` part gives the viewer. `frameFor` still drops `STEP_*` frames, snapshots and every step of the child, and a child's `RUN_FINISHED` (which carries no answer) is passed without the shared-answer reshaping. Slack and external audiences are unchanged. The viewer is offered no Stop or Details and reads no owner route (D14); the job's own stream already admits the share audience.

**D5. A loop sub-agent is named by its goal.** The timeline's child view takes `goal` from its `spawn_agent` call (as `codeTaskGoal` comes from `code_task`), carried on `RUN_STARTED`, the saved `sub_agent` part and the activity's child (`AgentHarnessActivityStep.child.goal` in the OpenAPI). The card and panel use it; a durable child never shows its role id (an older journal without a goal reads "Background job").

## As built

- An ended engine job opened from its card costs one timeline read in total (CJ-E1); a reopened chat at most one.
- A card drawn again keeps what the job said, including a refused Stop.
- Tests: `lib/harness-jobs/__tests__/cjfixb-job-state.test.ts`, `job-card/__tests__/cjfixb-job-card.test.tsx`, `activity-panel/__tests__/cjfixb-job-panel.test.tsx`, Node `tests/modules/agent-harness/projection/cjfixb-viewer-and-goal.test.ts`; NK-6 and TL-6 updated to this intent.

## Deviations

- A shared viewer's page still reads `GET /agents/:key` and gets a quiet 404 when the viewer may not open the chat's agent: that read is how the page learns whether it may show the agent's name and menu. The conversation read should say so instead (CJFIXB-FU-1); the spec allows only this one refusal.

## Owner amendments

None. The lead decided this from first principles within the classic jobs plan (owner direction 2026-11-10).

## Status

**Accepted** 2026-11-10 (CJFIX-B).
