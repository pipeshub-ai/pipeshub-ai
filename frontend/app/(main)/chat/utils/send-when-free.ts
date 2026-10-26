import { chatExtension } from '@/config';
import { useChatStore } from '@/app/(main)/chat/store';
import type { ChatSlot } from '@/app/(main)/chat/types';
import { ATTACHMENT_ONLY_STREAM_QUERY, buildStreamChatRequestForSlot } from '@/app/(main)/chat/runtime';
import { streamMessageForSlot } from '@/app/(main)/chat/streaming';
import { markSentFromQueue, queueOf, requeueFirst, takeFirst } from './queued-send';

/**
 * The page is going away (a reload, the tab closing). The answer it cuts short frees the chat, but a question sent then
 * is cut too, after it left the queue: it stays queued instead, and the tab's copy brings it back. A leave that did not
 * happen (a cancelled prompt) is forgotten after a moment, since the page is still here to run the timer.
 */
let leaving = false;
const LEAVE_FORGOTTEN_MS = 2000;
if (typeof window !== 'undefined') {
  const leave = () => {
    leaving = true;
    window.setTimeout(() => {
      leaving = false;
    }, LEAVE_FORGOTTEN_MS);
  };
  window.addEventListener('beforeunload', leave);
  window.addEventListener('pagehide', leave);
}

/**
 * Whether the slot's first queued question may go now: nothing runs or stops in the chat, nothing refused waits ahead
 * of it, and the extension holds nothing back.
 */
export function queueMayGo(slot: ChatSlot | undefined): boolean {
  const first = queueOf(slot)[0];
  return Boolean(
    slot &&
      first &&
      !first.failed &&
      !slot.isStreaming &&
      !slot.stopping &&
      !slot.remoteRunInProgress &&
      !chatExtension.queue.isHeld(slot),
  );
}

/**
 * Sends the first queued question now. It leaves the queue as it goes, so it goes out once; a refusal puts it back.
 * Returns whether a send started.
 */
export function sendNextQueued(slotId: string): boolean {
  if (leaving || !queueMayGo(useChatStore.getState().slots[slotId])) return false;
  const queued = takeFirst(slotId);
  if (!queued) return false;
  const apiQuery = queued.query || (queued.attachments?.length ? ATTACHMENT_ONLY_STREAM_QUERY : '');
  // Nothing to ask (an empty message never queues): it is dropped.
  if (!apiQuery) return false;
  const request = buildStreamChatRequestForSlot(slotId, apiQuery, undefined, undefined, queued.mentionIds?.length ? { mentionIds: queued.mentionIds } : undefined);
  if (!request) {
    requeueFirst(slotId, queued);
    return false;
  }
  if (queued.attachments?.length) request.attachments = queued.attachments;
  Object.assign(request, chatExtension.queue.requestFields(queued));
  markSentFromQueue(queued, request);
  void streamMessageForSlot(slotId, queued.query, request);
  // The stream marks the slot before its first await; one that did not start leaves the question first in line.
  if (!useChatStore.getState().slots[slotId]?.isStreaming) {
    requeueFirst(slotId, queued);
    return false;
  }
  return true;
}
