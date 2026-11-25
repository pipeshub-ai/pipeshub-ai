# ADR-UI-02: Every card sends its answer one way, and a failed send says why

- Package: R26-CARDFAIL (STATUS CARDFAIL-1).
- Related: ADR-RUN-03 (the answer contract; its pre-run question card), ADR-OWD-09 (approvals), DECIDE409 (one
  request for a batch), ADR-OWD-17 (engine sign-in).
- Finding it answers: a manual test, 2026-11-11 (ANSWER-CONTRACT-SKIP). While the app restarted, the person's
  three answers to a question card got 503 `HTTP_AGENT_HARNESS_RUN_CONFIG_UNAVAILABLE` (Node logged
  `admission.refused reason=unavailable`: the query service was not up yet). The card stayed as it was, with no
  reason and no retry, so it looked like the same question asked again and again.

## Context

A chat asks the person through cards: a question (`ask_user`, the pre-run grounding card, a job's question on the
chat's card), an approval of any kind (a call, a batch, a connect, a plan, an unknown effect, a job's ask on the
floating card or in its panel), and an engine sign-in's code. Each sent its answer its own way:

- the question card sent a chat turn; a failure put the card back and raised a toast that faded;
- an approval retried once on a 409 or no answer, then said "could not be sent";
- a job panel's approval and the sign-in code did not retry at all.

None of them told a passing failure (a restart, a dropped connection) from a refusal with a reason, and none said
which it was on the card. The fault was shared: there was no one place that sends a card's answer.

## Decision

D1. **One send.** Every card sends through `frontend/app/(main)/chat/card-send.ts` (`sendCard`), keyed by the card
(an approval's `approvalKey`, a question's assistant row, a sign-in's attempt). The send state lives in one store,
so the inline card, the floating card and a job's panel read the same state, and a second send of a card that is
sending is not started.

D2. **Two kinds of failure.** No answer, or a 5xx (502/503/504 read as "PipesHub is restarting"), is transient: the
card retries it by itself, with waits of 1, 2, 4, 8 and then 15 s, about 75 s in all, jittered, while the person's
choice stays as they made it, and says "Couldn't send your answer: PipesHub is restarting. Trying again…". After
the last wait it says the same reason with Try again. A 4xx with a reason is a refusal: the card says why (an
approval's `REFUSAL_KEYS`, a question's admission words) and never retries. A 409 `REVISION_CONFLICT` (an older
server's run that moved under the decision) stays transient, as DECIDE409 had it.

D3. **Only what the server recognises as the same is resent unasked.** A decision carries its idempotency key (the
decision itself), so a retry replays the first decision. A question's answer keeps its turn's `runId` across its
retries (a new answer gets a new one), and Node admits a turn once per run id (`turnIdempotencyKey`). An engine
sign-in's code is single-use at Anthropic and the worker cannot tell a second copy from a new code, so its card
offers Try again at once and nothing is resent unasked.

D4. **Answered elsewhere stops the send.** A card resolved while its send waits (another tab, another approver, the
run ended) stops retrying and shows that decision; a message typed instead of an answer drops the question's
pending send.

## As built

- `card-send.ts`: `sendFailureOf` (status and code to transient or refusal), `sendCard` (the loop, the waits and the
  store), `cancelCardSend`, `useCardSend`; `CardSendNotice` (`message-area/card-send-notice.tsx`) draws the three
  states and is drawn by `ApprovalInteraction` (every approval kind), the floating job ask, a job panel's approval,
  the question card and the sign-in card.
- Approvals: `decideApproval`, `decideApprovals` and the job panel's decision post once each attempt
  (`postDecision` no longer retries inside).
- Question: `question-answer.ts` (`answerQuestionCard`) sends the turn through `streamMessageForSlot` with the kept
  `runId` and `onSendOutcome`, which says whether the turn reached the server (its first frame) or failed first; a
  failure the card says shows no toast.
- Node: no change was needed; the chat routes already admit a turn once per body `runId`, and decisions once per
  key (SD-3). `backend/nodejs/apps/tests/modules/agent-harness/cardfail-idempotent-answer.http.test.ts` pins the
  answer case: refused 503 with nothing written, then the same answer twice, is one run.

## Deviations

- The sign-in code is not retried by itself (D3). Making it so would need the worker to remember a code's result
  per attempt; a code is short-lived, so the person's Try again is the honest choice.

## Owner amendments

None yet.

## Status

**Accepted** 2026-11-11: decided from first principles, long term, UX-first.
