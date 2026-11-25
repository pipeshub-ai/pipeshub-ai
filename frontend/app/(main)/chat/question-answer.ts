'use client';

import { useChatStore } from './store';
import { buildStreamChatRequestForSlot } from './runtime';
import { streamMessageForSlot } from './streaming';
import { cancelCardSend, cardSendRunning, clearCardSend, questionSendKey, SEND_ABORTED, sendCard, type SendAttempt } from './card-send';
import type { AskUserQuestionAnswer } from './types';

/**
 * One answer's turn id, kept while the same answer is sent again (CARDFAIL): the server admits a turn once per run id
 * (`turnIdempotencyKey`), so a retry after a lost reply replays the first and never answers twice. A different answer
 * is a new turn.
 */
const answerIds = new Map<string, { message: string; runId: string }>();

function answerRunId(key: string, message: string): string | undefined {
  const known = answerIds.get(key);
  if (known && known.message === message) return known.runId;
  const runId = globalThis.crypto?.randomUUID?.();
  if (runId) answerIds.set(key, { message, runId });
  return runId;
}

/**
 * Sends the person's answer to the slot's open question card through the cards' shared send: a transient failure
 * (PipesHub restarting, no connection) keeps the card with the person's choice and retries with backoff, then offers
 * Try again; a refusal says why and the card can be answered again. Answering twice sends once.
 */
export async function answerQuestionCard(
  slotId: string,
  message: string,
  answers: Record<string, AskUserQuestionAnswer>,
): Promise<void> {
  const pending = useChatStore.getState().slots[slotId]?.pendingAskUserQuestion;
  if (!pending || pending.status !== 'pending') return;
  const key = questionSendKey(pending.assistantMessageId);
  if (cardSendRunning(key)) return;
  clearCardSend(key);
  const runId = answerRunId(key, message);
  const attempt = (): Promise<SendAttempt<true>> =>
    new Promise((resolve) => {
      const p = useChatStore.getState().slots[slotId]?.pendingAskUserQuestion;
      const request = p ? buildStreamChatRequestForSlot(slotId, message) : null;
      if (!p || !request) {
        resolve({ failed: { retry: false, code: SEND_ABORTED } });
        return;
      }
      useChatStore.getState().updateSlot(slotId, { pendingAskUserQuestion: { ...p, answers, status: 'submitted' } });
      // A stream that ends without a first frame or a failure was held back before it started (another tab's run
      // still writes the thread); the card stays sent, as it always did, rather than offering a second answer.
      void Promise.resolve(
        streamMessageForSlot(slotId, message, request, {
          resumeAskUserQuestion: true,
          ...(runId ? { runId } : {}),
          onSendOutcome: resolve,
        }),
      ).finally(() => resolve({ sent: true }));
    });
  const outcome = await sendCard(key, attempt, {
    again: () => {
      reopen(slotId);
      void answerQuestionCard(slotId, message, answers);
    },
    keepRefusal: true,
  });
  if ('sent' in outcome) {
    answerIds.delete(key);
    return;
  }
  if ('cancelled' in outcome) return;
  if ('refused' in outcome && outcome.refused === SEND_ABORTED) cancelCardSend(key);
  // Not sent: the card is open again, with the person's choice, for Try again or another answer.
  reopen(slotId);
}

function reopen(slotId: string): void {
  const p = useChatStore.getState().slots[slotId]?.pendingAskUserQuestion;
  if (p && p.status === 'submitted') {
    useChatStore.getState().updateSlot(slotId, { pendingAskUserQuestion: { ...p, status: 'pending' } });
  }
}
