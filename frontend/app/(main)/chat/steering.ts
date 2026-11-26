/**
 * Steering (P1b T2): a message sent while the chat shows a durable run is
 * added to that running task instead of starting a second one. The server
 * queues it into the run, which reads it at its next step; the stream then
 * shows it in the run's timeline where it was taken.
 */
import { ChatApi, type SteerOutcome } from './api';
import { ATTACHMENT_ONLY_STREAM_QUERY, buildStreamChatRequestForSlot } from './runtime';
import { useChatStore } from './store';
import type { AttachmentRef, ChatSlot } from './types';

/** How long a message whose task just ended waits for that task's stream to close. */
const IDLE_WAIT_MS = 15_000;

/** The slot follows a durable run that can still take input (not being stopped). */
export function canSteerSlot(slot: ChatSlot | undefined): boolean {
  return Boolean(slot?.isStreaming && slot.durableRun && !slot.stopping);
}

/** Resolves once `slotId` streams nothing, or after `timeoutMs` (Infinity: no limit). */
export function untilSlotIdle(slotId: string, timeoutMs = IDLE_WAIT_MS): Promise<void> {
  return new Promise((resolve) => {
    const idle = (): boolean => !useChatStore.getState().slots[slotId]?.isStreaming;
    if (idle()) {
      resolve();
      return;
    }
    const timer = Number.isFinite(timeoutMs) ? setTimeout(done, timeoutMs) : undefined;
    const unsubscribe = useChatStore.subscribe(() => {
      if (idle()) done();
    });
    function done(): void {
      clearTimeout(timer);
      unsubscribe();
      resolve();
    }
  });
}

/**
 * Sends `query` and its uploaded `attachments` (by reference; the server
 * checks the user can read them) to the task `slotId` is running.
 * `not_active`: the task ended meanwhile; this resolves once its stream
 * closed, so the caller can send the message as a new turn.
 */
export async function steerRunningTask(
  slotId: string,
  query: string,
  attachments: AttachmentRef[] = [],
): Promise<SteerOutcome> {
  const slot = useChatStore.getState().slots[slotId];
  const run = slot?.durableRun;
  const text = query || (attachments.length > 0 ? ATTACHMENT_ONLY_STREAM_QUERY : '');
  const request = text ? buildStreamChatRequestForSlot(slotId, text) : null;
  if (!run || !request) return { status: 'refused', code: null };
  const outcome = await ChatApi.steerDurableRun(
    { ...request, conversationId: run.sessionId, ...(attachments.length > 0 ? { attachments } : {}) },
    run,
  );
  if (outcome.status === 'not_active') await untilSlotIdle(slotId);
  return outcome;
}
