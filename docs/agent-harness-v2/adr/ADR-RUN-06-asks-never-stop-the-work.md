# ADR-RUN-06: An ask never stops the work

- Related: ADR-RUN-01 D4 (the run's time plan) and D7 (the research-run scenario lane), ADR-RUN-03 (the answer
  contract: a *question* still ends the turn), ADR-REPO-01 (public repositories on by default), G-12 (connect waits),
  E2 FB-3 (an approval is named by the call's effect key, so a re-call finds its decision).
- Found by: R39-RESEARCH, from a manual test of 2026-11-13 (a research report that was less thorough than earlier ones).

## Context

A manual test, 2026-11-13, Codex job `49a69fc6` (gpt-6-luna): 33 s into the job the engine ran `ph git clone`. The clone was held
for the person (`APPROVAL_HOLD_S`, 120 s) while Codex went on searching, then came back deferred with the gateway's
words "End this turn; do not call the tool again until you are told the decision". The engine ended its turn at 3 min
22 s (`engine.kernel.turn_ended deferred=1`) and the job sat suspended for the 40 minutes the person took to allow it.
Turn 2 (`restored=state`) was told only "Call each approved tool again"; its restored session still held turn 1's
time plan ("gather until 07:5x UTC, then write; finished by 07:5x"), whose clock times had passed while it waited. It
cloned, made 35 page reads and 10 searches and wrote a 40 KB report in 8 minutes, by its own choice: no limit, steer or cut fired.

Repository clones became an everyday ask on 2026-11-12 (ADR-REPO-01: public hosts on by default where web access is
on). Earlier research runs never met this path: their approver allowed each clone within the hold.

The fault is shared by every engine ask that is not a question: an approval and a connect wait both said "End this
turn", so an engine that still had work to do stopped, and a decision was told to the engine only in a later turn.

## Decision

**D1. A held call tells the engine to keep working.** The gateway's deferred result (an approval past the hold, a
connect wait) says: keep working on the rest of the task meanwhile; the decision comes as a message, in this turn if
you are still working; do not call the tool again until told; end your turn only when nothing is left that can be done
without it (`WHILE_WAITING`, `harness_core/gateway/endpoint.py`). `ph` prints the gateway's words on exit 75, so a
shell clone reads the same. A question (`ask_user`) is unchanged: its answer is the person's next message
(ADR-RUN-03 D2).

**D2. A decision reaches the turn that is still running.** The kernel's watch already syncs the run's journal while
a turn runs. When a call the turn deferred is decided, the kernel words it once (`_decisions_note`, the same words a
next turn gets) and hands it over:
- steered into the turn (`EngineController.tell` → `EngineSession.steer`) where the engine can be steered (Codex,
  Claude Code);
- otherwise opening the next result the engine is handed (opencode, the ACP engines), as the writing window's words
  already ride on results for those engines (WP-70).

The engine calls the approved tool again; the approval is named by the call's effect key, so the re-call runs at once.
A decision handed over is not told again: the turn's end suspends or re-drives only on what it still owes
(`_TrackingEndpoint.unrelayed`). A turn that ends before the decision suspends as before and the next turn is told.
The relay is not journaled: a turn re-driven after a lost attempt tells the decision at its next turn, as before.

**D3. The hold stays.** 120 s gives a person at the screen an inline answer, and engines that run shell commands in the
background (Codex) are not blocked by it. Shortening it is not needed once the wait no longer ends the turn.

**D4. A later turn gets an allowance of its own.** A follow-up turn on a restored session states this turn's
allowance and time plan, saying it replaces any earlier plan (`RENEWED_ALLOWANCE`). Before, only a first turn or one
that lost its history was told; a restored session kept clock times that a person's wait had put in the past.

**Consequences.**

- An engine job is idle on an ask only when it has nothing else to do. The research journey (RUN06-2, R39-DEPTH-1):
  ask to clone, keep reading, the person allows later, the clone is fetched, checked out and cited in the same turn.
- The engine's own `/pipeshub/AGENTS.md` (in the run images) said "End your turn" for exit 75 until R44-SMALL
  (R39RES-FU-1): its exit-75 row now carries the gateway's own words (`WHILE_WAITING`), a test holds the two equal,
  and the run images moved to `-ph7`. SR-17's task instructions are not the page and are unchanged.
- A real model's depth is still the model's. The harness's share is held by the research-depth guard
  (`ResearchDepth`, R39-DEPTH-*, RUN01-SC-13).

## As built

- `harness_core/gateway/endpoint.py` (`WHILE_WAITING`), `harness_core/engines/durable/kernel.py`
  (`_relay_decisions`, `_TrackingEndpoint.waiting/unrelayed/carry`, `RENEWED_ALLOWANCE`),
  `harness_core/engines/controller.py` (`tell`).
- Tests: `tests/unit/harness_core/engines/durable/test_run06_asks_keep_working.py` (RUN06-1..6, steered and on the
  next result), `tests/unit/harness/engines/test_r39_research_depth.py` (R39-DEPTH-1..4),
  `test_run01_research_scenario.py::test_run01_sc13_*`. The scripted model keeps working on a held call whose words
  say so and calls an approved one again (`harness_core/testing/engines/script.py`).

## Deviations

- The relay is not journaled (D2): a decision told mid-turn by an attempt that then dies is told again at the next
  turn, by the decisions note the turn's saved state still owes.
- The real-stack journey for a card answered after the hold is open (R39RES-FU-4); the cells are tracked as a gap in
  `journeys.json` (`approval.after_hold`).

### Amendment R47-POLISH (2026-11-14): a run's web search pauses, it never turns off

R39RES-FU-3. `web_search` stops after 4 searches in a row that found nothing or failed (`MAX_UNPRODUCTIVE_SEARCHES`,
main #4570). A chat answer builds the tool per request, so that ends one answer's searching, which is right: the
person is waiting and a fifth try rarely helps. An engine run builds it once for the whole job (`web_scope="run"`,
the run's allowance of 150 searches), so a provider's bad minute ended search for a 30-minute research job: the same
fault this ADR fixes for asks, a passing condition stopping the work.

Options weighed: reset the count on a productive search (already so; it does not help when the provider is down for
a minute); scale the limit with the allowance (a down provider then burns many searches and model turns before the
stop, and the stop is still for good); count only empty results (a provider that fails every call would be asked on
every turn). Decided, the person's job first and the provider's spend bounded:

- **D5.** For a run-scoped tool the 4th unproductive search in a row starts a pause (60 s) instead of a give-up. While
  paused the tool answers at once, without calling the provider, with `error_type: "search_paused"`, `retryable:
  true`, `retry_after_s` and words that tell the model to keep working on what needs no search and to search again
  after the pause (a model told "turned off" stops trying, which is what lost the job its search). After the pause one
  search goes out; unproductive, the next pause is twice the last, capped at 15 minutes, so a provider that is truly
  down costs one probe per pause; productive, the count and the pause start again. The run's own allowance still
  caps every search. A chat answer's give-up is unchanged.

As built: `app/web/tools/web_search.py` (`scope`, `FIRST_PAUSE_S`, `MAX_PAUSE_S`), the tool loader passes the
budget's scope. Tests: `tests/unit/web/test_r47_run_search_pauses.py` (RES3-1..6, the engine's tool set and the step card included).

## Owner amendments

None. Decided by the package on the owner's direction of 2026-11-12 (first principles, the person's experience first).

## Status

**Accepted** (R39-RESEARCH, 2026-11-13).
